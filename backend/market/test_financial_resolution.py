"""Financial recovery uses isolated databases and provider doubles, never live HTTP."""
import csv
import io
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import Mock, patch

from django.contrib.auth.models import Permission, User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import close_old_connections, connection, connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from .errors import BusinessError
from .financial import hold_code
from .financial_export import reconciliation_export
from .models import FinancialEvidence, MerchantProfile, Order, PaymentAttempt, PaymentRefund, Stall, WorkerHeartbeat
from .payments import _apply_refund, request_refund, sync_payment
from .reconciliation import request_operator_refund, verify_and_resolve, verify_external_refund
from .serializers import OrderSerializer
from .services import merchant_action
from .test_payments import PaymentSetup, payment_result, refund_result
from .wechatpay import GatewayError
from .payment_test_utils import deliver_notification as handle_notification


class ResolutionSetup(PaymentSetup):
    def setUp(self):
        super().setUp()
        self.operator = User.objects.create_user('finance-operator', password='FinancialTest123!', is_staff=True)
        self.operator.user_permissions.add(Permission.objects.get(codename='resolve_payments'))

    def paid_gateway(self):
        def query(number):
            payment = PaymentAttempt.objects.get(out_trade_no=number)
            return payment_result(payment, transaction_id=payment.transaction_id or 'WX' + payment.out_trade_no)
        self.gateway.query_payment.side_effect = query

    def close_refund(self):
        payment = self.pay()
        request_refund(self.order.pk, self.vendor, '客户取消取餐')
        original = PaymentRefund.objects.get(order=self.order)
        self.gateway.query_refund.side_effect = lambda number: refund_result(PaymentRefund.objects.get(out_refund_no=number), 'CLOSED')
        sync_payment(self.order.pk)
        self.paid_gateway()
        original.refresh_from_db()
        return payment, original

    def allow_new_refund(self, original=None):
        def query(number):
            if original and number == original.out_refund_no:
                return refund_result(PaymentRefund.objects.get(pk=original.pk), 'CLOSED')
            refund = PaymentRefund.objects.get(out_refund_no=number)
            if refund.status != 'creating':
                return refund_result(refund, 'PROCESSING')
            raise GatewayError('RESOURCE_NOT_EXISTS', '尚未创建')
        self.gateway.query_refund.side_effect = query

    def notify_refund(self, refund, state='SUCCESS'):
        self.gateway.verify_notification.return_value = {'id': uuid.uuid4().hex, 'event_type': 'REFUND.' + state,
            'resource': refund_result(refund, state, notification=True)}
        handle_notification('own-account', {}, b'{}')

    def external_result(self, payment, number='EXTERNAL-FULL', **changes):
        return {**dict(mchid=payment.mchid, out_trade_no=payment.out_trade_no,
            transaction_id=payment.transaction_id, out_refund_no=number, refund_id='WX-'+number+'-'+payment.mchid,
            amount={'total': payment.amount_cents, 'refund': payment.amount_cents, 'currency': 'CNY'},
            status='SUCCESS', success_time=timezone.now().isoformat()), **changes}


class ExternalRefundPacingTests(ResolutionSetup, TestCase):
    def setUp(self):
        super().setUp()
        pacing = override_settings(PAYMENT_QUERY_INTERVAL_SECONDS=5, PAYMENT_QUERY_FAILURE_DELAYS=(10, 20, 30, 60))
        pacing.enable()
        self.addCleanup(pacing.disable)
        self.payment = self.pay()
        self.paid_gateway()

    def existing_refund(self, **fields):
        return PaymentRefund.objects.create(order=self.order, payment=self.payment,
            out_refund_no='EXTERNAL-PACING', amount_cents=self.payment.amount_cents,
            reason='隔离核验', status='processing', **fields)

    def test_known_refund_respects_its_own_cooldown(self):
        refund = self.existing_refund(next_query_at=timezone.now() + timedelta(seconds=60))
        self.gateway.query_refund.side_effect = lambda number: refund_result(refund)
        with self.assertRaises(BusinessError) as caught:
            verify_external_refund(self.order.pk, self.payment.pk, refund.out_refund_no, self.operator, '核验')
        self.assertEqual(caught.exception.detail['code'], 'payment_query_cooldown')
        self.gateway.query_refund.assert_not_called()
        self.gateway.query_payment.assert_not_called()

    def test_known_refund_respects_an_active_worker_lease(self):
        refund = self.existing_refund(request_in_flight_until=timezone.now() + timedelta(seconds=60))
        self.gateway.query_refund.side_effect = lambda number: refund_result(refund)
        with self.assertRaises(BusinessError) as caught:
            verify_external_refund(self.order.pk, self.payment.pk, refund.out_refund_no, self.operator, '核验')
        self.assertEqual(caught.exception.detail['code'], 'financial_operation_busy')
        self.gateway.query_refund.assert_not_called()
        self.gateway.query_payment.assert_not_called()

    def test_unknown_refund_failures_back_off_without_creating_financial_records(self):
        self.gateway.query_refund.side_effect = GatewayError('NETWORK_UNKNOWN', '待确认', retryable=True)
        now = timezone.now()
        for attempt, delay in enumerate((10, 20, 30, 60), start=1):
            with patch('django.utils.timezone.now', return_value=now):
                with self.assertRaises(BusinessError):
                    verify_external_refund(self.order.pk, self.payment.pk, 'UNKNOWN-EXTERNAL', self.operator, '核验')
                with self.assertRaises(BusinessError) as caught:
                    verify_external_refund(self.order.pk, self.payment.pk, 'ANOTHER-UNKNOWN', self.operator, '换号核验')
                self.assertEqual(caught.exception.detail['code'], 'payment_query_cooldown')
            self.payment.refresh_from_db()
            self.assertEqual(self.payment.next_query_at, now + timedelta(seconds=delay))
            self.assertEqual(self.payment.consecutive_query_failures, attempt)
            self.assertIsNone(self.payment.request_in_flight_until)
            self.assertFalse(PaymentRefund.objects.exists())
            self.assertEqual(self.gateway.query_refund.call_count, attempt)
            now = self.payment.next_query_at

    def test_unknown_refund_keeps_lease_and_success_seeds_refund_cooldown(self):
        from .reconciliation import _observe
        now = timezone.now()
        def query(number):
            self.payment.refresh_from_db()
            self.assertGreater(self.payment.request_in_flight_until, now)
            with self.assertRaises(BusinessError) as caught:
                _observe(self.order.pk, self.payment.pk, self.operator, '并发付款核验')
            self.assertEqual(caught.exception.detail['code'], 'financial_operation_busy')
            return self.external_result(self.payment, number)
        self.gateway.query_refund.side_effect = query
        with patch('django.utils.timezone.now', return_value=now):
            verify_external_refund(self.order.pk, self.payment.pk, 'NEW-EXTERNAL', self.operator, '核验')
            with self.assertRaises(BusinessError) as caught:
                verify_external_refund(self.order.pk, self.payment.pk, 'NEW-EXTERNAL', self.operator, '重试')
            self.assertEqual(caught.exception.detail['code'], 'payment_query_cooldown')
        refund = PaymentRefund.objects.get()
        self.payment.refresh_from_db()
        self.assertEqual(refund.status, 'success')
        self.assertEqual(refund.next_query_at, now + timedelta(seconds=5))
        self.assertEqual(self.payment.next_query_at, now + timedelta(seconds=5))
        self.assertIsNone(self.payment.request_in_flight_until)
        self.gateway.query_refund.assert_called_once()

    def test_unknown_refund_lost_lease_cannot_apply_success_or_reset_schedule(self):
        future = timezone.now() + timedelta(minutes=3)
        def query(number):
            PaymentAttempt.objects.filter(pk=self.payment.pk).update(
                request_in_flight_until=future, next_query_at=future, consecutive_query_failures=4)
            return self.external_result(self.payment, number)
        self.gateway.query_refund.side_effect = query
        with self.assertRaises(BusinessError) as caught:
            verify_external_refund(self.order.pk, self.payment.pk, 'LEASE-LOST', self.operator, '核验')
        self.assertEqual(caught.exception.detail['code'], 'financial_operation_busy')
        self.payment.refresh_from_db()
        self.assertEqual((self.payment.request_in_flight_until, self.payment.next_query_at,
            self.payment.consecutive_query_failures), (future, future, 4))
        self.assertFalse(PaymentRefund.objects.exists())

    def test_unknown_refund_does_not_overwrite_a_record_created_during_query(self):
        future = timezone.now() + timedelta(minutes=3)
        def query(number):
            PaymentRefund.objects.create(order=self.order, payment=self.payment, out_refund_no=number,
                amount_cents=self.payment.amount_cents, reason='另一操作', status='processing',
                next_query_at=future, request_in_flight_until=future)
            return self.external_result(self.payment, number)
        self.gateway.query_refund.side_effect = query
        with self.assertRaises(BusinessError):
            verify_external_refund(self.order.pk, self.payment.pk, 'CREATED-DURING-QUERY', self.operator, '核验')
        refund = PaymentRefund.objects.get()
        self.assertEqual((refund.status, refund.next_query_at, refund.request_in_flight_until),
            ('processing', future, future))
        self.payment.refresh_from_db()
        self.assertIsNone(self.payment.request_in_flight_until)
        self.assertEqual(self.payment.consecutive_query_failures, 1)


class FinancialResolutionTests(ResolutionSetup, TestCase):
    def permission_actor(self, *permissions):
        actor = User.objects.create_user('capability-operator', is_staff=True)
        actor.user_permissions.set(Permission.objects.filter(codename__in=permissions))
        self.api.force_authenticate(actor)
        return actor

    def test_readonly_operator_has_no_mutation_capabilities_in_list_or_pickup_lookup(self):
        self.permission_actor('view_order')
        listed = self.api.get('/api/v1/merchant/orders')
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.data[0]['allowed_actions'], [])
        lookup = self.api.post(f'/api/v1/merchant/stalls/{self.stall.pk}/pickup-lookup',
            {'pickup_code': self.order.pickup_code}, format='json')
        self.assertEqual(lookup.status_code, 200)
        self.assertEqual(lookup.data['allowed_actions'], [])
        self.assertFalse(lookup.data['payment_can_close'])
        self.assertEqual(lookup.data['pickup_code'], '')
        denied = self.api.post(f'/api/v1/merchant/orders/{self.order.pk}/action', {'action': 'confirm_payment'})
        self.assertEqual(denied.status_code, 404)

    def test_change_order_operator_response_does_not_advertise_refund_permission(self):
        self.pay()
        self.permission_actor('view_order', 'change_order')
        listed = self.api.get('/api/v1/merchant/orders').data[0]
        self.assertIn('complete', listed['allowed_actions'])
        self.assertNotIn('refund', listed['allowed_actions'])
        self.assertNotIn('sync_payment', listed['allowed_actions'])
        denied = self.api.post(f'/api/v1/merchant/orders/{self.order.pk}/refund', {'reason': '无退款权限'})
        self.assertEqual(denied.status_code, 404)
        completed = self.api.post(f'/api/v1/merchant/orders/{self.order.pk}/action',
            {'action': 'complete', 'pickup_code': self.order.pickup_code})
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.data['status'], 'completed')
        self.assertNotIn('refund', completed.data['allowed_actions'])

    def test_refund_operator_has_only_financial_capabilities_in_payment_response(self):
        self.pay()
        self.permission_actor('view_order', 'add_paymentrefund')
        listed = self.api.get('/api/v1/merchant/orders').data[0]
        self.assertIn('refund', listed['allowed_actions'])
        self.assertNotIn('complete', listed['allowed_actions'])
        denied = self.api.post(f'/api/v1/merchant/orders/{self.order.pk}/action',
            {'action': 'complete', 'pickup_code': self.order.pickup_code})
        self.assertEqual(denied.status_code, 404)
        result = self.api.post(f'/api/v1/merchant/orders/{self.order.pk}/refund', {'reason': '核准退款'})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data['allowed_actions'], ['sync_payment'])
        self.assertEqual(result.data['pickup_code'], '')

    def test_student_payment_response_capabilities_and_links_require_actual_owner(self):
        started = self.api.post(f'/api/v1/orders/{self.order.pk}/payments/wechat', {'channel': 'native'})
        self.assertEqual(started.status_code, 200)
        self.assertIn('pay', started.data['allowed_actions'])
        self.assertIn('close_payment', started.data['allowed_actions'])
        self.assertTrue(started.data['payment_can_close'])
        self.assertTrue(started.data['payment']['code_url'])
        self.refresh()
        foreign = OrderSerializer(self.order, context={'request': SimpleNamespace(user=self.operator)}).data
        self.assertEqual(foreign['allowed_actions'], [])
        self.assertFalse(foreign['payment_can_close'])
        self.assertEqual(foreign['payment']['code_url'], '')
        self.assertEqual(foreign['pickup_code'], '')
        self.api.force_authenticate(self.operator)
        self.assertEqual(self.api.post(f'/api/v1/orders/{self.order.pk}/payments/sync').status_code, 404)

    def test_closed_pickup_refund_blocks_server_pickup_and_hides_code(self):
        payment, original = self.close_refund()
        self.refresh()
        self.assertEqual((self.order.status, self.order.payment_status, original.status), ('ready', 'paid', 'closed'))
        data = OrderSerializer(self.order).data
        self.assertEqual(data['pickup_code'], '')
        self.assertTrue(data['financial_hold_reason'])
        self.assertNotIn('complete', OrderSerializer(self.order, context={'merchant': True}).data['allowed_actions'])
        with self.assertRaises(BusinessError) as error:
            merchant_action(self.order.pk, self.vendor, 'complete', self.order.pickup_code)
        self.assertEqual(error.exception.detail['code'], 'refund_unresolved')

    def test_closed_retry_has_new_number_preserves_history_and_idempotently_refunds_once(self):
        payment, original = self.close_refund()
        self.allow_new_refund(original)
        arguments = (self.order.pk, payment.pk, self.operator, '核对关闭后重试', 'retry-closed-once', 800)
        request_operator_refund(*arguments, replaces_id=original.pk)
        new = PaymentRefund.objects.exclude(pk=original.pk).get(order=self.order)
        request_operator_refund(*arguments, replaces_id=original.pk)
        self.assertNotEqual(new.out_refund_no, original.out_refund_no)
        self.assertEqual(new.replaces_id, original.pk)
        self.assertEqual(PaymentRefund.objects.count(), 2)
        self.assertEqual(self.gateway.refund.call_count, 2)  # original + authorized retry
        self.notify_refund(new)
        original.refresh_from_db(); self.refresh()
        self.assertEqual(original.status, 'closed')
        self.assertIsNotNone(original.resolved_at)
        self.assertEqual((self.order.status, self.order.payment_status), ('cancelled', 'refunded'))
        self.assertEqual(self.order.payment_refund.pk, new.pk)
        self.assertEqual(self.product.stock, 4)

    def test_external_full_refund_is_verified_then_all_facts_are_required_to_resolve(self):
        payment = self.pay(); self.paid_gateway()
        Order.objects.filter(pk=self.order.pk).update(payment_review_required=True)
        self.gateway.query_refund.side_effect = lambda number: self.external_result(payment, number)
        verify_external_refund(self.order.pk, payment.pk, 'EXTERNAL-FULL', self.operator, '商户平台退款核验')
        self.refresh()
        self.assertTrue(self.order.payment_review_required)
        self.assertEqual(self.order.payment_status, 'refunded')
        verify_and_resolve(self.order.pk, self.operator, '核对全部付款退款及履约')
        self.refresh()
        self.assertFalse(self.order.payment_review_required)
        self.assertEqual(self.order.status, 'cancelled')
        verify_external_refund(self.order.pk, payment.pk, 'EXTERNAL-FULL', self.operator, '重复核验原流水')
        self.assertEqual(PaymentRefund.objects.count(), 1)
        self.assertTrue(FinancialEvidence.objects.filter(operation='financial_resolution', outcome='resolved').exists())

    def test_external_refund_with_wrong_identity_amount_currency_or_signature_never_resolves(self):
        payment = self.pay(); self.paid_gateway()
        Order.objects.filter(pk=self.order.pk).update(payment_review_required=True)
        for changes in ({'mchid': 'wrong'}, {'transaction_id': 'wrong'},
                        {'amount': {'total': 800, 'refund': 400, 'currency': 'CNY'}},
                        {'amount': {'total': 800, 'refund': 800, 'currency': 'USD'}}, {'status': 'PROCESSING'}):
            self.gateway.query_refund.side_effect = None
            self.gateway.query_refund.return_value = self.external_result(payment, **changes)
            with self.assertRaises(BusinessError):
                verify_external_refund(self.order.pk, payment.pk, 'EXTERNAL-FULL', self.operator, '核验')
        self.gateway.query_refund.side_effect = GatewayError('INVALID_PAYMENT_SIGNATURE', '未通过签名核验')
        with self.assertRaises(BusinessError):
            verify_external_refund(self.order.pk, payment.pk, 'EXTERNAL-FULL', self.operator, '核验')
        self.assertEqual(PaymentRefund.objects.count(), 0)
        self.refresh(); self.assertTrue(self.order.payment_review_required)
        self.assertEqual(FinancialEvidence.objects.filter(operation='external_refund_checked', outcome='unverified').count(), 6)

    def test_late_closed_after_external_success_resolves_original_without_rewriting_its_state(self):
        payment = self.pay(); self.paid_gateway()
        request_refund(self.order.pk, self.vendor, '退款')
        original = PaymentRefund.objects.get(order=self.order)
        Order.objects.filter(pk=self.order.pk).update(payment_review_required=True)
        self.gateway.query_refund.side_effect = lambda number: self.external_result(payment, number)
        verify_external_refund(self.order.pk, payment.pk, 'EXTERNAL-RETRY', self.operator, '原关闭通知滞后，外部全额重退已成功')
        original.refresh_from_db(); self.assertEqual(original.status, 'processing')
        self.notify_refund(original, 'CLOSED')
        original.refresh_from_db()
        self.assertEqual(original.status, 'closed'); self.assertIsNotNone(original.resolved_at)
        self.refresh(); self.assertEqual((self.order.status, self.order.payment_status), ('cancelled', 'refunded'))
        self.gateway.query_refund.side_effect = lambda number: (refund_result(original, 'CLOSED')
            if number == original.out_refund_no else self.external_result(payment, number))
        verify_and_resolve(self.order.pk, self.operator, '核对迟到关闭及全额重退')
        self.refresh(); self.assertFalse(self.order.payment_review_required)

    def test_extra_capture_can_be_compensated_without_refunding_the_legitimate_payment(self):
        primary = self.pay(); self.paid_gateway()
        extra = PaymentAttempt.objects.create(order=self.order, merchant=self.stall.merchant, mode='live',
            account_key=primary.account_key, mchid=primary.mchid, appid=primary.appid,
            out_trade_no='EXTRA'+uuid.uuid4().hex[:20], transaction_id='EXTRA-TX', amount_cents=800, status='paid',
            channel='native', expires_at=timezone.now()+timedelta(minutes=5), paid_at=timezone.now())
        Order.objects.filter(pk=self.order.pk).update(payment_review_required=True)
        self.allow_new_refund()
        request_operator_refund(self.order.pk, extra.pk, self.operator, '补偿重复扣款', 'compensate-extra', 800)
        refund = PaymentRefund.objects.get(payment=extra)
        self.notify_refund(refund)
        self.gateway.query_refund.side_effect = lambda number: refund_result(PaymentRefund.objects.get(out_refund_no=number))
        verify_and_resolve(self.order.pk, self.operator, '主付款保留，额外付款已全额退回')
        self.refresh()
        self.assertEqual((self.order.status, self.order.payment_status), ('ready', 'paid'))
        self.assertFalse(self.order.payment_review_required)
        self.assertFalse(PaymentRefund.objects.filter(payment=primary).exists())
        merchant_action(self.order.pk, self.vendor, 'complete', self.order.pickup_code)

    def test_cancelled_order_with_net_capture_cannot_be_resolved_without_compensation(self):
        self.pay(); self.paid_gateway()
        Order.objects.filter(pk=self.order.pk).update(status='cancelled', payment_review_required=True)
        with self.assertRaises(BusinessError): verify_and_resolve(self.order.pk, self.operator, '核对')
        self.refresh(); self.assertTrue(self.order.payment_review_required)

    def test_successful_refunds_cannot_exceed_a_capture(self):
        payment = self.pay(); self.paid_gateway()
        self.gateway.query_refund.side_effect = lambda number: self.external_result(payment, number)
        verify_external_refund(self.order.pk, payment.pk, 'FIRST-FULL', self.operator, '核验外部退款')
        with self.assertRaises(BusinessError):
            verify_external_refund(self.order.pk, payment.pk, 'SECOND-FULL', self.operator, '不应重复计入')
        self.assertEqual(PaymentRefund.objects.filter(status='success').count(), 1)

    def test_staff_without_financial_permission_and_merchant_are_denied_before_gateway(self):
        payment = self.pay()
        readonly = User.objects.create_user('readonly-finance', is_staff=True)
        for actor in (self.vendor, self.student, readonly):
            self.gateway.reset_mock()
            with self.assertRaises(BusinessError) as error:
                verify_and_resolve(self.order.pk, actor, '不应操作')
            self.assertEqual(error.exception.status_code, 403)
            with self.assertRaises(BusinessError):
                request_operator_refund(self.order.pk, payment.pk, actor, '不应操作', 'not-authorized', 800)
            self.gateway.query_payment.assert_not_called(); self.gateway.refund.assert_not_called()

    def test_evidence_is_append_only_and_export_separates_money_without_customer_details(self):
        self.pay(); self.paid_gateway()
        verify_and_resolve(self.order.pk, self.operator, '核对正常收款')
        value = FinancialEvidence.objects.first()
        value.reason = 'rewrite'
        with self.assertRaises(ValidationError): value.save()
        with self.assertRaises(ValidationError): FinancialEvidence.objects.filter(pk=value.pk).update(reason='rewrite')
        with self.assertRaises(ValidationError): value.delete()
        with self.assertRaises(ValidationError): FinancialEvidence.objects.filter(pk=value.pk).delete()
        day = timezone.localdate()
        with self.assertRaises(BusinessError): reconciliation_export(self.operator, self.stall.merchant_id, day, day)
        self.operator.user_permissions.add(Permission.objects.get(codename='export_financial_reconciliation'))
        self.operator = User.objects.get(pk=self.operator.pk)
        result = reconciliation_export(self.operator, self.stall.merchant_id, day, day)
        text = b''.join(result.streaming_content).decode('utf-8-sig')
        rows = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual(rows[0]['record_type'], 'wechat_payment')
        self.assertNotIn('contact_phone', text)
        self.assertNotIn('pickup_code', text)
        self.assertEqual(rows[0]['amount_cents'], '800')

    def test_operator_admin_requires_explicit_refund_confirmation(self):
        payment, original = self.close_refund()
        self.api.force_authenticate(None)
        self.api.force_login(self.operator)
        response = self.api.post(f'/admin/market/order/{self.order.pk}/financial-resolution/', {
            'operation': 'retry', 'payment': payment.pk, 'refund': original.pk, 'reason': '重试',
            'expected_amount_cents': 800, 'operation_key': 'admin-retry-guard', 'confirm_refund': False})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '申请退款须确认原付款与金额')
        self.assertEqual(PaymentRefund.objects.count(), 1)

    def test_different_original_merchants_can_import_same_external_refund_number(self):
        first = self.pay(); self.paid_gateway()
        merchant = MerchantProfile.objects.create(user=self.other, business_name='另一独立商户', is_verified=True,
            qualification_tier='storefront', licensed_business_address='门店', food_preparation_address='后厨')
        stall = Stall.objects.create(merchant=merchant, area=self.stall.area, name='另一摊位', category='小吃')
        order = Order.objects.create(user=self.student, stall=stall, stall_name=stall.name, status='ready',
            payment_method='wechat', payment_status='paid', total_cents=800, paid_at=timezone.now(),
            expires_at=timezone.now()+timedelta(minutes=5), pickup_address='独立位置', pickup_latitude=0,
            pickup_longitude=0, idempotency_key='other-merchant-payment', request_hash='test')
        second = PaymentAttempt.objects.create(order=order, merchant=merchant, account_key='other-account',
            mchid='9876543210', appid='otherApp', out_trade_no='OTHER'+uuid.uuid4().hex[:20], transaction_id='OTHER-TX',
            channel='native', amount_cents=800, status='paid', expires_at=order.expires_at, paid_at=order.paid_at)
        other_gateway = Mock(mchid=second.mchid, appid=second.appid)
        other_gateway.query_payment.side_effect = lambda number: payment_result(second, transaction_id=second.transaction_id)
        other_gateway.query_refund.side_effect = lambda number: self.external_result(second, number)
        self.gateway.query_refund.side_effect = lambda number: self.external_result(first, number)
        with patch('market.payments.client_for', side_effect=lambda key: other_gateway if key == 'other-account' else self.gateway):
            verify_external_refund(self.order.pk, first.pk, 'SHARED-EXTERNAL-NUMBER', self.operator, '核验第一商户')
            verify_external_refund(order.pk, second.pk, 'SHARED-EXTERNAL-NUMBER', self.operator, '核验第二商户')
        self.assertEqual(PaymentRefund.objects.filter(out_refund_no='SHARED-EXTERNAL-NUMBER', status='success').count(), 2)

    def test_export_keeps_old_unresolved_differences_and_excludes_other_merchants_and_simulation(self):
        self.operator.user_permissions.add(Permission.objects.get(codename='export_financial_reconciliation'))
        Order.objects.filter(pk=self.order.pk).update(created_at=timezone.now()-timedelta(days=90),
            payment_review_required=True, number='=SUM(1,2)')
        foreign_merchant = MerchantProfile.objects.create(user=self.other, business_name='other')
        foreign_stall = Stall.objects.create(merchant=foreign_merchant, area=self.stall.area, name='other', category='x')
        for mode, stall, key in [('live', foreign_stall, 'foreign-difference'), ('simulation', self.stall, 'simulation-difference')]:
            Order.objects.create(user=self.student, stall=stall, stall_name=stall.name, mode=mode, number=key,
                status='cancelled', payment_review_required=True, total_cents=800,
                expires_at=timezone.now(), pickup_address='x', pickup_latitude=0, pickup_longitude=0,
                idempotency_key=key, request_hash='test')
        day = timezone.localdate()
        response = reconciliation_export(self.operator, self.stall.merchant_id, day, day)
        rows = list(csv.DictReader(io.StringIO(b''.join(response.streaming_content).decode('utf-8-sig'))))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['record_type'], 'unresolved_difference')
        self.assertEqual(rows[0]['order_number'], "'=SUM(1,2)")
        self.assertTrue(rows[0]['reason'])

    def test_external_refund_can_recover_capture_when_original_success_callback_was_lost(self):
        payment = self.start()
        captured = payment_result(payment)
        self.gateway.query_payment.side_effect = lambda number: {**captured, 'trade_state': 'REFUND'}
        payment.transaction_id = captured['transaction_id']
        self.gateway.query_refund.side_effect = lambda number: self.external_result(payment, number)
        verify_external_refund(self.order.pk, payment.pk, 'REFUNDED-BEFORE-CALLBACK', self.operator, '付款通知丢失后商户平台已全额退款')
        self.refresh(); payment.refresh_from_db()
        self.assertEqual(payment.status, 'paid')
        self.assertEqual(self.order.payment_status, 'refunded')
        self.assertTrue(self.order.payment_review_required)
        verify_and_resolve(self.order.pk, self.operator, '核验迟到的完整付款退款事实')
        self.refresh(); self.assertFalse(self.order.payment_review_required)

    def test_changed_merchant_configuration_cannot_verify_or_retry_old_payment(self):
        payment, original = self.close_refund()
        self.gateway.mchid = '0000000000'
        with self.assertRaises(BusinessError):
            request_operator_refund(self.order.pk, payment.pk, self.operator, '应使用原主体', 'wrong-merchant-key', 800, replaces_id=original.pk)
        self.assertEqual(PaymentRefund.objects.count(), 1)
        self.assertEqual(self.gateway.refund.call_count, 1)

    def test_unknown_original_refund_cannot_authorize_a_new_number(self):
        payment, original = self.close_refund()
        self.gateway.query_refund.side_effect = GatewayError('PAYMENT_NETWORK_UNKNOWN', '结果尚未核验')
        with self.assertRaises(BusinessError):
            request_operator_refund(self.order.pk, payment.pk, self.operator, '查询失败应保留原请求', 'unknown-retry-key', 800, replaces_id=original.pk)
        self.assertEqual(PaymentRefund.objects.count(), 1)
        self.assertEqual(self.gateway.refund.call_count, 1)

    def test_worker_keeps_closed_refunds_visible_and_reports_failure_without_claiming_success(self):
        _, original = self.close_refund()
        with patch('market.management.commands.reconcile_payments.sync_payment', side_effect=RuntimeError('must-not-log-sensitive-value')) as sync:
            stderr = io.StringIO()
            call_command('reconcile_payments', limit=1, stdout=io.StringIO(), stderr=stderr)
            sync.assert_called_once_with(self.order.pk)
        heartbeat = WorkerHeartbeat.objects.get(name='reconcile_payments')
        self.assertEqual(heartbeat.failure_count, 1)
        self.assertIsNone(heartbeat.last_success_at)
        self.assertNotIn('must-not-log-sensitive-value', stderr.getvalue())
        with patch('market.management.commands.reconcile_payments.sync_payment') as sync:
            call_command('reconcile_payments', limit=1, stdout=io.StringIO())
            sync.assert_called_once_with(self.order.pk)
        heartbeat.refresh_from_db()
        self.assertIsNotNone(heartbeat.last_success_at)
        self.assertEqual(heartbeat.last_error_code, '')
        original.refresh_from_db(); self.assertIsNone(original.resolved_at)

    def test_worker_skips_live_leases_and_identical_refund_polls_do_not_grow_evidence(self):
        payment = self.start()
        PaymentAttempt.objects.filter(pk=payment.pk).update(request_in_flight_until=timezone.now()+timedelta(seconds=60))
        with patch('market.management.commands.reconcile_payments.sync_payment') as sync:
            call_command('reconcile_payments', limit=1, stdout=io.StringIO())
            sync.assert_not_called()
        PaymentAttempt.objects.filter(pk=payment.pk).update(request_in_flight_until=None)
        self.gateway.verify_notification.return_value = {'id': uuid.uuid4().hex, 'event_type': 'TRANSACTION.SUCCESS', 'resource': payment_result(payment)}
        handle_notification('own-account', {}, b'{}')
        request_refund(self.order.pk, self.vendor, '退款')
        before = FinancialEvidence.objects.count()
        sync_payment(self.order.pk); sync_payment(self.order.pk)
        self.assertEqual(FinancialEvidence.objects.count(), before)


@skipUnless(connection.vendor == 'postgresql', 'Financial retry races need real PostgreSQL row locks.')
class FinancialResolutionConcurrencyTests(ResolutionSetup, TransactionTestCase):
    def test_external_refund_http_keeps_one_lease_without_holding_order_lock(self):
        payment = self.pay()
        self.paid_gateway()
        entered, release = Event(), Event()
        def query(number):
            self.assertFalse(connection.in_atomic_block)
            entered.set()
            if not release.wait(timeout=10):
                raise AssertionError('External query release timed out')
            return self.external_result(payment, number)
        self.gateway.query_refund.side_effect = query
        def run():
            close_old_connections()
            try:
                return verify_external_refund(self.order.pk, payment.pk, 'EXTERNAL-PARALLEL',
                    self.operator, '并发核验').pk
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(run)
            try:
                self.assertTrue(entered.wait(timeout=10))
                with transaction.atomic():
                    Order.objects.select_for_update(nowait=True).get(pk=self.order.pk)
                with self.assertRaises(BusinessError) as caught:
                    verify_external_refund(self.order.pk, payment.pk, 'EXTERNAL-OTHER-NUMBER',
                        self.operator, '另一退款号')
                self.assertEqual(caught.exception.detail['code'], 'financial_operation_busy')
                self.gateway.query_refund.assert_called_once()
            finally:
                release.set()
            self.assertEqual(future.result(timeout=10), self.order.pk)
        self.assertEqual(PaymentRefund.objects.count(), 1)
        self.assertEqual(PaymentRefund.objects.get().status, 'success')

    def test_two_operator_retry_keys_cannot_create_two_active_refunds(self):
        payment, original = self.close_refund()
        self.allow_new_refund(original)
        gate = Barrier(2)
        def run(key):
            close_old_connections()
            try:
                gate.wait(timeout=10)
                request_operator_refund(self.order.pk, payment.pk, self.operator, '并发重试', key, 800, replaces_id=original.pk)
                return 'ok'
            except BusinessError as exc:
                return exc.detail['code']
            finally: connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, ['parallel-retry-one', 'parallel-retry-two']))
        self.assertIn('ok', results)
        self.assertEqual(PaymentRefund.objects.filter(payment=payment).count(), 2)
        self.assertEqual(PaymentRefund.objects.filter(payment=payment, status__in=PaymentRefund.ACTIVE_STATUSES).count(), 1)
        self.assertEqual(self.gateway.refund.call_count, 2)

    def test_operator_verification_http_does_not_hold_order_transaction(self):
        self.pay()
        def query(number):
            self.assertFalse(connection.in_atomic_block)
            return payment_result(PaymentAttempt.objects.get(out_trade_no=number))
        self.gateway.query_payment.side_effect = query
        verify_and_resolve(self.order.pk, self.operator, '事务外核款')
