import uuid
"""Live WeChat responses below use isolated test doubles; rehearsal is tested in test_simulation.py."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import skipUnless
from unittest.mock import Mock, patch

from django.test import TestCase, TransactionTestCase, override_settings
from django.core.management import call_command
from django.db import close_old_connections, connection, connections
from django.utils import timezone
from rest_framework.test import APIClient

from .errors import BusinessError
from .models import AuditLog, Order, PaymentAttempt, PaymentRefund
from .payments import close_payment, request_refund, start_payment, sync_payment
from .services import cancel_order, create_order, merchant_action
from .tests import fixtures, payload
from .wechatpay import GatewayError
from .payment_test_utils import deliver_notification as handle_notification


def payment_result(payment, state='SUCCESS', **changes):
    result = {'appid': payment.appid, 'mchid': payment.mchid, 'out_trade_no': payment.out_trade_no,
        'trade_state': state, 'amount': {'total': payment.amount_cents, 'currency': 'CNY'}}
    if state == 'SUCCESS':
        result.update(transaction_id='WX' + payment.out_trade_no,
            success_time=timezone.now().isoformat())
    result.update(changes)
    return result


def refund_result(refund, state='SUCCESS', notification=False, **changes):
    result = {'mchid': refund.payment.mchid, 'out_trade_no': refund.payment.out_trade_no,
        'transaction_id': refund.payment.transaction_id, 'out_refund_no': refund.out_refund_no,
        'refund_id': 'WX' + refund.out_refund_no,
        'amount': {'total': refund.payment.amount_cents, 'refund': refund.amount_cents, 'currency': 'CNY'}}
    result['refund_status' if notification else 'status'] = state
    if notification: result['amount'].pop('currency')
    if state == 'SUCCESS': result['success_time'] = timezone.now().isoformat()
    result.update(changes)
    return result


class PaymentSetup:
    def setUp(self):
        # These tests exercise financial transitions; default pacing has separate clock/concurrency regressions.
        pacing = override_settings(PAYMENT_QUERY_INTERVAL_SECONDS=0, PAYMENT_QUERY_FAILURE_DELAYS=(0,),
            CHECKOUT_MAX_ACTIVE_PER_STALL=100, CHECKOUT_MAX_ACTIVE_TOTAL=100)
        pacing.enable(); self.addCleanup(pacing.disable)
        self.student, self.other, self.vendor, self.stall, self.product = fixtures()
        self.order, _ = create_order(self.student, payload(self.stall, self.product))
        merchant_action(self.order.pk, self.vendor, 'accept')
        merchant_action(self.order.pk, self.vendor, 'ready')
        self.order.refresh_from_db()
        self.gateway = Mock(mchid='1234567890', appid='wxTestApp', enabled=True, channels=('native', 'h5'))
        self.gateway.create_payment.return_value = {'code_url': 'weixin://wxpay/test-code'}
        self.gateway.close_payment.return_value = {}
        def query_payment(number):
            if not self.gateway.create_payment.call_count:
                raise GatewayError('ORDER_NOT_EXIST', '尚未收到创建请求')
            return payment_result(PaymentAttempt.objects.get(out_trade_no=number), 'NOTPAY')
        self.gateway.query_payment.side_effect = query_payment
        self.gateway.refund.side_effect = lambda **kwargs: refund_result(PaymentRefund.objects.get(out_refund_no=kwargs['out_refund_no']), 'PROCESSING')
        def query_refund(number):
            if not self.gateway.refund.call_count:
                raise GatewayError('RESOURCE_NOT_EXISTS', '尚未收到退款请求')
            return refund_result(PaymentRefund.objects.get(out_refund_no=number), 'PROCESSING')
        self.gateway.query_refund.side_effect = query_refund
        self.patches = [patch('market.payments.client_for', return_value=self.gateway),
            patch('market.payments.readiness', return_value={'available': True, 'reason': '', 'channels': ['native', 'h5'], 'account_key': 'own-account'})]
        for item in self.patches: item.start(); self.addCleanup(item.stop)
        self.api = APIClient()
        self.api.force_authenticate(self.student)

    def start(self):
        start_payment(self.order.pk, self.student, 'native', '127.0.0.1')
        return PaymentAttempt.objects.get(order=self.order)

    def pay(self):
        payment = self.start()
        self.gateway.verify_notification.return_value = {'id': uuid.uuid4().hex, 'event_type': 'TRANSACTION.SUCCESS', 'resource': payment_result(payment)}
        handle_notification('own-account', {}, b'{}')
        payment.refresh_from_db()
        return payment

    def refresh(self):
        self.order.refresh_from_db(); self.product.refresh_from_db()


class PaymentTests(PaymentSetup, TestCase):
    def test_simulation_channel_cannot_recover_a_live_payment(self):
        self.start()
        self.gateway.reset_mock()
        response = self.api.post(f'/api/v1/orders/{self.order.pk}/payments/wechat', {'channel': 'simulation'})
        self.assertEqual(response.status_code, 400)
        self.gateway.query_payment.assert_not_called()
        self.gateway.create_payment.assert_not_called()

    def test_missing_configuration_fails_closed_without_intent(self):
        with patch('market.payments.readiness', return_value={'available': False, 'reason': '未开通', 'channels': [], 'account_key': ''}):
            result = self.api.post(f'/api/v1/orders/{self.order.pk}/payments/wechat', {'channel': 'native'})
        self.assertEqual(result.status_code, 503)
        self.assertFalse(PaymentAttempt.objects.exists())
        self.gateway.create_payment.assert_not_called()
        self.refresh(); self.assertEqual(self.order.payment_method, 'offline')

    def test_local_gateway_configuration_and_h5_ip_rejected_before_intent(self):
        self.gateway.enabled = False
        with self.assertRaises(BusinessError): self.start()
        self.gateway.enabled = True
        with self.assertRaises(BusinessError): start_payment(self.order.pk, self.student, 'h5', '')
        self.assertFalse(PaymentAttempt.objects.exists())
        self.gateway.create_payment.assert_not_called()

    @override_settings(WECHAT_PAY_TRUST_PROXY_CLIENT_IP=False)
    def test_h5_does_not_trust_injected_forwarding_header(self):
        self.gateway.create_payment.return_value = {'h5_url': 'https://wx.tenpay.com/pay/test'}
        response = self.api.post(f'/api/v1/orders/{self.order.pk}/payments/wechat', {'channel': 'h5'},
            REMOTE_ADDR='192.0.2.1', HTTP_X_REAL_IP='203.0.113.99')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.gateway.create_payment.call_args.kwargs['payer_client_ip'], '192.0.2.1')

    def test_payment_mutations_need_csrf_but_callback_uses_signature(self):
        real_session = APIClient(enforce_csrf_checks=True)
        real_session.force_login(self.student)
        response = real_session.post(f'/api/v1/orders/{self.order.pk}/payments/wechat', {'channel': 'native'})
        self.assertEqual(response.status_code, 403)
        payment = self.start()
        self.gateway.verify_notification.return_value = {'id': uuid.uuid4().hex, 'event_type': 'TRANSACTION.SUCCESS', 'resource': payment_result(payment)}
        response = real_session.post('/api/v1/payments/wechat/notify/own-account', '{}', content_type='application/json')
        self.assertEqual(response.status_code, 204)

    def test_stall_public_readiness_never_exposes_account_key(self):
        response = self.api.get(f'/api/v1/stalls/{self.stall.pk}')
        self.assertEqual(response.data['wechat_payment'], {'mode': 'live', 'available': True, 'reason': '', 'channels': ['native', 'h5']})
        self.assertNotIn('account_key', str(response.data))

    @override_settings(DEMO_MODE=False)
    def test_demo_stall_cannot_charge_even_if_configuration_enabled(self):
        self.stall.is_demo = True; self.stall.save()
        with self.assertRaises(BusinessError): self.start()
        self.gateway.create_payment.assert_not_called()

    def test_other_account_and_guest_cannot_create_query_or_close(self):
        for user, expected in [(self.other, 404), (None, 403)]:
            self.api.force_authenticate(user)
            for action in ('wechat', 'sync', 'close'):
                response = self.api.post(f'/api/v1/orders/{self.order.pk}/payments/{action}', {'channel': 'native'})
                self.assertEqual(response.status_code, expected)

    def test_payment_only_ready_no_cancellation_and_no_paid_order(self):
        for status in ('pending', 'preparing', 'completed', 'cancelled', 'rejected'):
            Order.objects.filter(pk=self.order.pk).update(status=status)
            with self.assertRaises(BusinessError): self.start()
        Order.objects.filter(pk=self.order.pk).update(status='ready', cancel_requested=True)
        with self.assertRaises(BusinessError): self.start()
        Order.objects.filter(pk=self.order.pk).update(cancel_requested=False, payment_status='paid')
        with self.assertRaises(BusinessError): self.start()
        self.gateway.create_payment.assert_not_called()

    def test_repeated_payment_reuses_one_intent_and_snapshot(self):
        first = self.start(); second = self.start()
        self.assertEqual(first.pk, second.pk)
        self.gateway.create_payment.assert_called_once()
        self.assertEqual(first.amount_cents, self.order.total_cents)
        self.refresh(); self.assertEqual(self.product.stock, 4)
        response = self.api.get(f'/api/v1/orders/{self.order.pk}')
        self.assertTrue(response.data['payment_can_close'])
        self.assertEqual(response.data['payment_method'], 'wechat')
        self.assertNotIn('account_key', str(response.data))
        self.assertNotIn('mchid', str(response.data))

    def test_unknown_create_remains_held_and_same_number_can_retry(self):
        self.gateway.create_payment.side_effect = GatewayError('PAYMENT_NETWORK_UNKNOWN', '结果待确认', retryable=True)
        payment = self.start()
        self.assertEqual(payment.status, 'reconcile')
        for action in ('confirm_payment', 'complete', 'approve_cancel'):
            with self.assertRaises(BusinessError): merchant_action(self.order.pk, self.vendor, action, self.order.pickup_code)
        with self.assertRaises(BusinessError): cancel_order(self.order.pk, self.student, '')
        self.gateway.query_payment.side_effect = GatewayError('ORDER_NOT_EXIST', '未找到')
        self.gateway.create_payment.side_effect = None
        retried = self.start()
        self.assertEqual(retried.pk, payment.pk)
        calls = self.gateway.create_payment.call_args_list
        self.assertEqual(calls[0].kwargs['out_trade_no'], calls[1].kwargs['out_trade_no'])
        self.assertEqual(retried.status, 'pending')

    def test_verified_success_is_idempotent_and_requires_pickup_code(self):
        payment = self.pay()
        handle_notification('own-account', {}, b'{}')
        self.refresh()
        self.assertEqual(self.order.payment_status, 'paid')
        self.assertEqual(self.order.status, 'ready')
        self.assertEqual(AuditLog.objects.filter(action='wechat_payment_confirmed').count(), 1)
        with self.assertRaises(BusinessError): merchant_action(self.order.pk, self.vendor, 'confirm_payment')
        with self.assertRaises(BusinessError): merchant_action(self.order.pk, self.vendor, 'complete', 'bad-code')
        merchant_action(self.order.pk, self.vendor, 'complete', self.order.pickup_code)
        self.refresh(); self.assertEqual(self.order.status, 'completed')
        with self.assertRaises(BusinessError): merchant_action(self.order.pk, self.vendor, 'complete', self.order.pickup_code)

    def test_payment_mismatch_never_marks_paid(self):
        payment = self.start()
        invalid = [dict(amount={'total': 1, 'currency': 'CNY'}), dict(amount={'total': 800, 'currency': 'USD'}),
            dict(mchid='wrong'), dict(appid='wrong'), dict(out_trade_no='wrong'), dict(transaction_id=''),
            dict(amount={'total': True, 'currency': 'CNY'}), dict(success_time='2026-40-01T00:00:00Z')]
        for change in invalid:
            self.gateway.query_payment.side_effect = None
            self.gateway.query_payment.return_value = payment_result(payment, **change)
            sync_payment(self.order.pk, self.student)
            self.refresh(); self.assertEqual(self.order.payment_status, 'unpaid')
        payment.refresh_from_db(); self.assertEqual(payment.status, 'reconcile')

    def test_callback_invalid_signature_and_foreign_account_rejected(self):
        payment = self.start()
        self.gateway.verify_notification.side_effect = GatewayError('INVALID_SIGNATURE', '未验证')
        response = self.api.post('/api/v1/payments/wechat/notify/own-account', '{}', content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.gateway.verify_notification.side_effect = None
        self.gateway.verify_notification.return_value = {'id': uuid.uuid4().hex, 'event_type': 'TRANSACTION.SUCCESS', 'resource': payment_result(payment)}
        response = self.api.post('/api/v1/payments/wechat/notify/other-account', '{}', content_type='application/json')
        # The signature double declares a valid receipt; ownership is rechecked
        # asynchronously before any order mutation, without locking HTTP ingress.
        self.assertEqual(response.status_code, 204)
        from .notification_inbox import process_notifications
        from .models import PaymentNotification
        self.assertEqual(process_notifications(), (0, 1))
        self.assertEqual(PaymentNotification.objects.get().status, 'dead')
        self.refresh(); self.assertEqual(self.order.payment_status, 'unpaid')

    def test_unpaid_query_can_omit_amount_and_confirmed_close_unblocks_offline(self):
        payment = self.start()
        data = payment_result(payment, 'NOTPAY'); data.pop('amount')
        self.gateway.query_payment.side_effect = None; self.gateway.query_payment.return_value = data
        close_payment(self.order.pk, self.student)
        payment.refresh_from_db(); self.refresh()
        self.assertEqual(payment.status, 'closed')
        self.assertEqual(self.order.payment_method, 'offline')
        merchant_action(self.order.pk, self.vendor, 'confirm_payment')

    def test_expired_local_clock_never_proves_gateway_closed(self):
        payment = self.start()
        PaymentAttempt.objects.filter(pk=payment.pk).update(expires_at=timezone.now()-timedelta(minutes=5))
        self.gateway.query_payment.side_effect = GatewayError('ORDER_NOT_EXIST', '结果未知')
        close_payment(self.order.pk, self.student)
        with self.assertRaises(BusinessError): cancel_order(self.order.pk, self.student, '')
        self.gateway.close_payment.assert_not_called()
        self.refresh(); self.assertEqual(self.product.stock, 4)

    def test_unknown_close_keeps_hold_then_successful_query_recovers(self):
        payment = self.start()
        self.gateway.close_payment.side_effect = GatewayError('PAYMENT_NETWORK_UNKNOWN', '结果未知')
        close_payment(self.order.pk, self.student)
        self.refresh(); self.assertEqual(self.order.payment_method, 'wechat')
        with self.assertRaises(BusinessError): merchant_action(self.order.pk, self.vendor, 'confirm_payment')
        self.gateway.query_payment.side_effect = None; self.gateway.query_payment.return_value = payment_result(payment)
        sync_payment(self.order.pk, self.student)
        self.refresh(); self.assertEqual(self.order.payment_status, 'paid')

    def test_closed_success_callback_is_recorded_without_reviving_cancelled_order(self):
        payment = self.start(); close_payment(self.order.pk, self.student)
        cancel_order(self.order.pk, self.student, '取消')
        merchant_action(self.order.pk, self.vendor, 'approve_cancel')
        self.gateway.verify_notification.return_value = {'id': uuid.uuid4().hex, 'event_type': 'TRANSACTION.SUCCESS', 'resource': payment_result(payment)}
        handle_notification('own-account', {}, b'{}')
        self.refresh(); payment.refresh_from_db()
        self.assertEqual(self.order.status, 'cancelled')
        self.assertTrue(self.order.payment_review_required)
        self.assertEqual(payment.status, 'paid')
        self.assertEqual(self.product.stock, 5)

    def test_paid_query_instead_of_close_prevents_switch_to_offline(self):
        payment = self.start()
        self.gateway.query_payment.side_effect = None; self.gateway.query_payment.return_value = payment_result(payment)
        close_payment(self.order.pk, self.student)
        self.gateway.close_payment.assert_not_called()
        self.refresh(); self.assertEqual((self.order.payment_method, self.order.payment_status), ('wechat', 'paid'))

    def test_full_refund_processing_then_success_no_restock_or_duplicate(self):
        self.pay()
        request_refund(self.order.pk, self.vendor, '用户无需取餐')
        refund = PaymentRefund.objects.get(order=self.order)
        self.refresh(); self.assertEqual(self.order.payment_status, 'refunding')
        with self.assertRaises(BusinessError): merchant_action(self.order.pk, self.vendor, 'complete', self.order.pickup_code)
        self.gateway.verify_notification.return_value = {'id': uuid.uuid4().hex, 'event_type': 'REFUND.SUCCESS', 'resource': refund_result(refund, notification=True)}
        handle_notification('own-account', {}, b'{}')
        handle_notification('own-account', {}, b'{}')
        request_refund(self.order.pk, self.vendor, '重复点击')
        self.refresh(); refund.refresh_from_db()
        self.assertEqual((self.order.status, self.order.payment_status), ('cancelled', 'refunded'))
        self.assertEqual(refund.status, 'success')
        self.assertEqual(self.product.stock, 4)
        self.assertFalse(self.order.inventory_released)
        self.gateway.refund.assert_called_once()
        self.assertEqual(AuditLog.objects.filter(action='wechat_refund_succeeded').count(), 1)

    def test_completed_order_refund_preserves_pickup_history(self):
        self.pay(); merchant_action(self.order.pk, self.vendor, 'complete', self.order.pickup_code)
        request_refund(self.order.pk, self.vendor, '售后退款')
        self.gateway.query_refund.side_effect = lambda number: refund_result(PaymentRefund.objects.get(out_refund_no=number))
        sync_payment(self.order.pk, self.student)
        self.refresh(); self.assertEqual((self.order.status, self.order.payment_status), ('completed', 'refunded'))
        self.assertIsNotNone(self.order.completed_at); self.assertEqual(self.product.stock, 4)

    def test_unknown_refund_retries_same_number_and_never_false_success(self):
        self.pay()
        self.gateway.refund.side_effect = GatewayError('PAYMENT_NETWORK_UNKNOWN', '退款待确认')
        request_refund(self.order.pk, self.vendor, '未取餐')
        refund = PaymentRefund.objects.get(order=self.order)
        self.assertEqual(refund.status, 'reconcile')
        self.refresh(); self.assertEqual(self.order.payment_status, 'refunding')
        self.gateway.query_refund.side_effect = GatewayError('RESOURCE_NOT_EXISTS', '暂未找到')
        self.gateway.refund.side_effect = lambda **kw: refund_result(PaymentRefund.objects.get(out_refund_no=kw['out_refund_no']), 'PROCESSING')
        request_refund(self.order.pk, self.vendor, '另一条原因不能改原快照')
        calls = self.gateway.refund.call_args_list
        self.assertEqual(calls[0].kwargs, calls[1].kwargs)
        self.assertEqual(PaymentRefund.objects.count(), 1)
        self.refresh(); self.assertEqual(self.order.payment_status, 'refunding')

    def test_refund_wrong_amount_and_receiver_fail_closed(self):
        self.pay(); request_refund(self.order.pk, self.vendor, '未取餐')
        refund = PaymentRefund.objects.get(order=self.order)
        for change in [dict(amount={'total': 800, 'refund': 1, 'currency': 'CNY'}), dict(mchid='wrong'), dict(transaction_id='wrong')]:
            self.gateway.query_refund.side_effect = None
            self.gateway.query_refund.return_value = refund_result(refund, **change)
            sync_payment(self.order.pk, self.student)
            self.refresh(); self.assertEqual(self.order.payment_status, 'refunding')
        refund.refresh_from_db(); self.assertEqual(refund.status, 'reconcile')

    def test_other_merchant_and_customer_cannot_refund(self):
        self.pay()
        for user in (self.other, self.student):
            with self.assertRaises(BusinessError): request_refund(self.order.pk, user, '退款')
        self.assertFalse(PaymentRefund.objects.exists())

    def test_offline_paid_order_cannot_use_wechat_refund(self):
        merchant_action(self.order.pk, self.vendor, 'confirm_payment')
        with self.assertRaises(BusinessError): request_refund(self.order.pk, self.vendor, '退款')
        self.gateway.refund.assert_not_called()

    def test_refund_api_validates_reason_and_preserves_merchant_pickup_privacy(self):
        self.pay(); self.api.force_authenticate(self.vendor)
        endpoint = f'/api/v1/merchant/orders/{self.order.pk}/refund'
        for reason in ('', '退'*30):
            self.assertEqual(self.api.post(endpoint, {'reason': reason}).status_code, 400)
        result = self.api.post(endpoint, {'reason': '用户协商退款'})
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['pickup_code'], '')
        self.assertEqual(result.data['refund']['status'], 'processing')

    def test_reconciliation_shares_budget_between_old_refunds_and_unchecked_payments(self):
        first = self.start()
        other_order, _ = create_order(self.student, payload(self.stall, self.product))
        merchant_action(other_order.pk, self.vendor, 'accept'); merchant_action(other_order.pk, self.vendor, 'ready')
        start_payment(other_order.pk, self.student, 'native', '127.0.0.1')
        other_payment = PaymentAttempt.objects.get(order=other_order)
        self.gateway.verify_notification.return_value = {'id': uuid.uuid4().hex, 'event_type': 'TRANSACTION.SUCCESS', 'resource': payment_result(other_payment)}
        handle_notification('own-account', {}, b'{}')
        request_refund(other_order.pk, self.vendor, '退款')
        PaymentRefund.objects.filter(order=other_order).update(last_checked_at=timezone.now())
        PaymentAttempt.objects.filter(pk=first.pk).update(last_checked_at=None)
        with patch('market.management.commands.reconcile_payments.sync_payment') as query:
            call_command('reconcile_payments', limit=1, verbosity=0)
            query.assert_called_once_with(self.order.pk)
        PaymentAttempt.objects.filter(pk=first.pk).update(last_checked_at=timezone.now())
        PaymentRefund.objects.filter(order=other_order).update(last_checked_at=None)
        with patch('market.management.commands.reconcile_payments.sync_payment') as query:
            call_command('reconcile_payments', limit=1, verbosity=0)
            query.assert_called_once_with(other_order.pk)


class PaymentDurabilityTests(PaymentSetup, TransactionTestCase):
    def test_process_failure_after_payment_preparation_keeps_queryable_intent(self):
        self.gateway.create_payment.side_effect = RuntimeError('simulated process crash')
        with self.assertRaises(RuntimeError): self.start()
        payment = PaymentAttempt.objects.get(order=self.order)
        self.assertEqual(payment.status, 'creating')
        self.refresh(); self.assertEqual(self.order.payment_method, 'wechat')
        with self.assertRaises(BusinessError): merchant_action(self.order.pk, self.vendor, 'confirm_payment')

    def test_process_failure_after_refund_preparation_keeps_queryable_refund(self):
        self.pay()
        self.gateway.refund.side_effect = RuntimeError('simulated process crash')
        with self.assertRaises(RuntimeError): request_refund(self.order.pk, self.vendor, '退餐')
        self.assertEqual(PaymentRefund.objects.get(order=self.order).status, 'creating')
        self.refresh(); self.assertEqual(self.order.payment_status, 'refunding')


@skipUnless(connection.vendor == 'postgresql', 'Payment race tests need real PostgreSQL row locks.')
class PaymentConcurrencyTests(PaymentSetup, TransactionTestCase):
    def race(self, left, right):
        barrier = Barrier(2)
        def run(fn):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return fn()
            except BusinessError as error: return error.detail['code']
            finally: connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run, fn) for fn in (left, right)]
            return [item.result(timeout=30) for item in futures]

    def test_payment_creation_and_cancel_are_mutually_exclusive(self):
        self.race(self.start, lambda: cancel_order(self.order.pk, self.student, '取消'))
        self.refresh()
        self.assertEqual(self.product.stock, 4)
        if PaymentAttempt.objects.exists():
            self.assertFalse(self.order.cancel_requested)
            self.assertEqual(self.order.payment_method, 'wechat')
        else:
            self.assertTrue(self.order.cancel_requested)

    def test_payment_notification_and_manual_payment_cannot_double_collect(self):
        payment = self.start()
        self.gateway.verify_notification.return_value = {'id': uuid.uuid4().hex, 'event_type': 'TRANSACTION.SUCCESS', 'resource': payment_result(payment)}
        self.race(lambda: handle_notification('own-account', {}, b'{}'),
            lambda: merchant_action(self.order.pk, self.vendor, 'confirm_payment'))
        self.refresh()
        self.assertEqual((self.order.payment_status, self.order.payment_method), ('paid', 'wechat'))
        self.assertEqual(AuditLog.objects.filter(action='order_confirm_payment').count(), 0)

    def test_repeated_create_reserves_one_external_number(self):
        self.race(self.start, self.start)
        self.assertEqual(PaymentAttempt.objects.count(), 1)
        self.gateway.create_payment.assert_called_once()

    def test_refund_and_pickup_are_serialized_with_refund_still_recorded(self):
        self.pay()
        self.race(lambda: request_refund(self.order.pk, self.vendor, '用户协商退款'),
            lambda: merchant_action(self.order.pk, self.vendor, 'complete', self.order.pickup_code))
        self.refresh()
        self.assertEqual(self.order.payment_status, 'refunding')
        self.assertIn(self.order.status, ('ready', 'completed'))
        self.assertEqual(PaymentRefund.objects.count(), 1)

    def test_start_and_offline_collection_cannot_both_succeed(self):
        self.race(self.start, lambda: merchant_action(self.order.pk, self.vendor, 'confirm_payment'))
        self.refresh()
        if PaymentAttempt.objects.exists():
            self.assertEqual((self.order.payment_method, self.order.payment_status), ('wechat', 'unpaid'))
            self.assertEqual(AuditLog.objects.filter(action='order_confirm_payment').count(), 0)
        else:
            self.assertEqual((self.order.payment_method, self.order.payment_status), ('offline', 'paid'))

    def test_duplicate_refunds_keep_one_external_refund_number(self):
        self.pay()
        self.race(lambda: request_refund(self.order.pk, self.vendor, '退款'),
            lambda: request_refund(self.order.pk, self.vendor, '退款'))
        self.assertEqual(PaymentRefund.objects.count(), 1)
        self.gateway.refund.assert_called_once()
