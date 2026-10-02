"""Delivery integration/races use an isolated test database and gateway double only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import time, timedelta
from threading import Barrier
from unittest import skipUnless
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .delivery import delivery_settings
from .errors import BusinessError
from .models import Area, DeliveryPoint, Order, PaymentAttempt, PaymentRefund, SiteConfiguration
from .payments import close_payment, handle_notification, request_refund, start_payment, sync_payment
from .services import cancel_order, confirm_receipt, create_order, expire_pending_orders, merchant_action
from .test_payments import payment_result, refund_result
from .tests import fixtures, payload
from .wechatpay import GatewayError


class DeliverySetup:
    def setUp(self):
        self.student, self.other, self.vendor, self.stall, self.product = fixtures()
        self.point = DeliveryPoint.objects.create(area=self.stall.area, name='南门交接点', address='南门校外交接台',
            latitude=31.231, longitude=121.471, is_active=True)
        self.stall.delivery_enabled = self.stall.delivery_approved = True
        self.stall.delivery_min_order_cents = 800
        self.stall.delivery_starts_at, self.stall.delivery_ends_at = time(0), time(23, 59, 59)
        self.stall.save(); self.stall.delivery_points.add(self.point)
        self.gateway = Mock(mchid='1234567890', appid='wxTestApp', enabled=True, channels=('native', 'h5'))
        self.gateway.create_payment.return_value = {'code_url': 'weixin://wxpay/test-code'}
        self.gateway.close_payment.return_value = {}
        self.gateway.query_payment.side_effect = lambda number: payment_result(PaymentAttempt.objects.get(out_trade_no=number), 'NOTPAY')
        self.gateway.query_refund.side_effect = GatewayError('RESOURCE_NOT_EXISTS', '尚未创建退款')
        self.gateway.refund.side_effect = lambda **kw: refund_result(PaymentRefund.objects.get(out_refund_no=kw['out_refund_no']), 'PROCESSING')
        for p in [patch('market.payments.client_for', return_value=self.gateway),
                  patch('market.payments.readiness', return_value={'available': True, 'reason': '', 'channels': ['native', 'h5'], 'account_key': 'own-account'})]:
            p.start(); self.addCleanup(p.stop)
        self.api = APIClient(); self.api.force_authenticate(self.student)

    def data(self, **changes):
        return {**payload(self.stall, self.product), 'fulfillment_type': 'delivery', 'delivery_point_id': self.point.pk,
            'expected_delivery_fee_cents': 300, 'recipient_name': '测试同学', 'contact_phone': '13800000000', **changes}

    def create(self, **changes): return create_order(self.student, self.data(**changes))[0]

    def pay(self, order):
        start_payment(order.pk, self.student, 'native', '127.0.0.1')
        payment = PaymentAttempt.objects.get(order=order)
        self.notify(payment)
        order.refresh_from_db()
        return payment

    def notify(self, payment, **changes):
        self.gateway.verify_notification.return_value = {'event_type': 'TRANSACTION.SUCCESS', 'resource': payment_result(payment, **changes)}
        handle_notification('own-account', {}, b'{}')

    def to_arrived(self, order):
        self.pay(order)
        for action in ('accept', 'ready', 'dispatch', 'arrive'):
            merchant_action(order.pk, self.vendor, action)
        order.refresh_from_db()

    def finish_refund(self, order):
        sync_payment(order.pk)
        refund = PaymentRefund.objects.get(order=order)
        self.gateway.verify_notification.return_value = {'event_type': 'REFUND.SUCCESS', 'resource': refund_result(refund, notification=True)}
        handle_notification('own-account', {}, b'{}')
        handle_notification('own-account', {}, b'{}')
        order.refresh_from_db(); self.product.refresh_from_db()
        return refund


class DeliveryTests(DeliverySetup, TestCase):
    def test_complete_delivery_only_after_verified_prepaid_and_actual_receipt(self):
        response = self.api.post('/api/v1/orders', self.data(), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        order = Order.objects.get(pk=response.data['id'])
        self.assertEqual((order.status, order.payment_method, order.total_cents), ('pending_payment', 'wechat', 1100))
        self.assertEqual(response.data['items_total_cents'], 800)
        for action in ('accept', 'confirm_payment', 'dispatch', 'arrive', 'complete'):
            with self.assertRaises(BusinessError): merchant_action(order.pk, self.vendor, action, order.pickup_code)
        self.pay(order)
        self.assertEqual(order.status, 'pending')
        self.assertLess(abs((order.expires_at-timezone.now()).total_seconds()-300), 5)
        for action in ('accept', 'ready', 'dispatch'):
            merchant_action(order.pk, self.vendor, action)
        with self.assertRaises(BusinessError): confirm_receipt(order.pk, self.student)
        merchant_action(order.pk, self.vendor, 'arrive')
        order.refresh_from_db(); self.assertEqual(order.status, 'arrived')
        self.assertIsNone(order.completed_at)
        with self.assertRaises(BusinessError): confirm_receipt(order.pk, self.other)
        response = self.api.post(f'/api/v1/orders/{order.pk}/confirm-receipt')
        self.assertEqual(response.data['status'], 'completed')
        self.assertIsNotNone(response.data['arrived_at'])
        self.assertEqual(self.api.post(f'/api/v1/orders/{order.pk}/confirm-receipt').status_code, 409)

    def test_merchant_receipt_code_is_one_time_and_never_in_merchant_payload(self):
        order = self.create(); self.to_arrived(order)
        self.api.force_authenticate(self.vendor)
        self.assertEqual(self.api.get('/api/v1/merchant/orders').data[0]['pickup_code'], '')
        with self.assertRaises(BusinessError): merchant_action(order.pk, self.other, 'complete', order.pickup_code)
        with self.assertRaises(BusinessError): merchant_action(order.pk, self.vendor, 'complete', 'wrong')
        merchant_action(order.pk, self.vendor, 'complete', order.pickup_code)
        with self.assertRaises(BusinessError): merchant_action(order.pk, self.vendor, 'complete', order.pickup_code)

    def test_missing_payment_or_approval_or_demo_fails_before_stock_reservation(self):
        for field, value in [('delivery_approved', False), ('delivery_enabled', False), ('is_demo', True), ('transaction_enabled', False)]:
            original = getattr(self.stall, field)
            setattr(self.stall, field, value); self.stall.save()
            with self.assertRaises(BusinessError): self.create()
            setattr(self.stall, field, original); self.stall.save()
        with patch('market.payments.readiness', return_value={'available': False, 'reason': '未开通', 'channels': []}):
            with self.assertRaises(BusinessError): self.create()
        self.assertFalse(Order.objects.exists()); self.product.refresh_from_db(); self.assertEqual(self.product.stock, 5)

    def test_stale_location_pauses_delivery(self):
        self.stall.current_session.last_confirmed_at = timezone.now()-timedelta(hours=2)
        self.stall.current_session.save()
        with self.assertRaises(BusinessError): self.create()

    def test_fee_minimum_point_and_capacity_validated_server_side(self):
        with self.assertRaises(BusinessError) as fee: self.create(expected_delivery_fee_cents=0)
        self.assertEqual(fee.exception.detail['code'], 'delivery_fee_changed')
        self.stall.delivery_min_order_cents = 900; self.stall.save()
        with self.assertRaises(BusinessError) as minimum: self.create()
        self.assertEqual(minimum.exception.detail['code'], 'delivery_minimum')
        self.stall.delivery_min_order_cents = 800; self.stall.delivery_capacity = 1; self.stall.save()
        with self.assertRaises(BusinessError): self.create(delivery_point_id=99999)
        order = self.create()
        with self.assertRaises(BusinessError): self.create()
        cancel_order(order.pk, self.student, '')
        self.create()

    def test_snapshot_and_idempotency_preserve_price_point_contact_and_fee(self):
        data = self.data()
        order, created = create_order(self.student, data)
        again, duplicate = create_order(self.student, data)
        self.assertTrue(created); self.assertFalse(duplicate); self.assertEqual(order.pk, again.pk)
        self.point.address = '新的交接台'; self.point.save()
        self.stall.delivery_fee_cents = 600; self.stall.save()
        saved = self.api.get(f'/api/v1/orders/{order.pk}').data
        self.assertEqual(saved['delivery_point_address'], '南门校外交接台')
        self.assertEqual(saved['delivery_fee_cents'], 300)
        self.assertEqual(saved['recipient_name'], '测试同学')
        with self.assertRaises(BusinessError): create_order(self.student, {**data, 'recipient_name': '另一位'})

    def test_delivery_requires_contact_and_approved_same_campus_point(self):
        for field in ('delivery_point_id', 'expected_delivery_fee_cents', 'recipient_name', 'contact_phone'):
            data = self.data(); data.pop(field)
            self.assertEqual(self.api.post('/api/v1/orders', data, format='json').status_code, 400)
        self.point.is_active = False; self.point.save()
        with self.assertRaises(BusinessError): self.create()

    def test_configuration_strict_owner_only_and_operator_approval_not_writable(self):
        endpoint = f'/api/v1/merchant/stalls/{self.stall.pk}/delivery'
        self.assertEqual(self.api.get(endpoint).status_code, 404)
        self.api.force_authenticate(self.vendor)
        self.assertEqual(self.api.patch(endpoint, {'approved': True}, format='json').status_code, 400)
        other_area = Area.objects.create(name='其他校园', latitude=30, longitude=120)
        foreign_point = DeliveryPoint.objects.create(area=other_area, name='其他校门', address='校门', latitude=30, longitude=120, is_active=True)
        self.assertEqual(self.api.patch(endpoint, {'point_ids': [foreign_point.pk]}, format='json').status_code, 400)
        self.assertEqual(self.api.patch(endpoint, {'starts_at': '18:00', 'ends_at': '08:00'}, format='json').status_code, 400)
        self.assertEqual(self.api.patch(endpoint, {'eta_min_minutes': 60, 'eta_max_minutes': 20}, format='json').status_code, 400)
        response = self.api.patch(endpoint, {'enabled': False, 'fee_cents': 500, 'point_ids': [self.point.pk]}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['available']); self.assertEqual(response.data['fee_cents'], 500)
        self.assertEqual(response.data['available_points'][0]['id'], self.point.pk)

    def test_point_coordinates_validated_in_operator_form(self):
        for field, value in [('latitude', float('nan')), ('longitude', float('inf')), ('latitude', 91), ('longitude', 181)]:
            point = DeliveryPoint(area=self.stall.area, name='点', address='地址', latitude=31, longitude=121)
            setattr(point, field, value)
            with self.assertRaises(ValidationError): point.full_clean()

    def test_expired_unpaid_without_intent_releases_once(self):
        order = self.create(); Order.objects.filter(pk=order.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(expire_pending_orders(), 1); self.assertEqual(expire_pending_orders(), 0)
        order.refresh_from_db(); self.product.refresh_from_db()
        self.assertEqual(order.status, 'cancelled'); self.assertIn('付款', order.cancel_reason)
        self.assertEqual(self.product.stock, 5)

    def test_last_minute_cannot_create_a_dead_payment_intent(self):
        order = self.create()
        Order.objects.filter(pk=order.pk).update(expires_at=timezone.now()+timedelta(seconds=30))
        with self.assertRaises(BusinessError): start_payment(order.pk, self.student, 'native', '127.0.0.1')
        self.assertFalse(PaymentAttempt.objects.exists()); self.gateway.create_payment.assert_not_called()
        cancel_order(order.pk, self.student, '')
        self.product.refresh_from_db(); self.assertEqual(self.product.stock, 5)

    def test_definitive_first_preflight_failure_releases_payment_hold_only(self):
        order = self.create()
        self.gateway.create_payment.side_effect = GatewayError('INVALID_PAYMENT_ARGUMENT', '剩余有效期不足', outcome_unknown=False)
        start_payment(order.pk, self.student, 'native', '127.0.0.1')
        payment = PaymentAttempt.objects.get(order=order)
        self.assertEqual(payment.status, 'closed')
        order.refresh_from_db(); self.assertEqual(order.payment_method, 'wechat')
        cancel_order(order.pk, self.student, '')
        self.product.refresh_from_db(); self.assertEqual(self.product.stock, 5)

    def test_expired_operation_lease_cannot_close_a_newer_unknown_attempt(self):
        order = self.create()
        renewed = timezone.now()+timedelta(minutes=5)
        def fail(**kwargs):
            PaymentAttempt.objects.filter(out_trade_no=kwargs['out_trade_no']).update(request_in_flight_until=renewed)
            raise GatewayError('INVALID_PAYMENT_ARGUMENT', '旧请求本地拒绝', outcome_unknown=False)
        self.gateway.create_payment.side_effect = fail
        start_payment(order.pk, self.student, 'native', '127.0.0.1')
        payment = PaymentAttempt.objects.get(order=order)
        self.assertEqual(payment.status, 'creating')
        self.assertEqual(payment.request_in_flight_until, renewed)
        with self.assertRaises(BusinessError): cancel_order(order.pk, self.student, '')

    def test_unknown_payment_stays_held_after_expiry(self):
        order = self.create()
        self.gateway.create_payment.side_effect = GatewayError('PAYMENT_NETWORK_UNKNOWN', '未知')
        start_payment(order.pk, self.student, 'native', '127.0.0.1')
        self.gateway.query_payment.side_effect = GatewayError('ORDER_NOT_EXIST', '暂未查到')
        Order.objects.filter(pk=order.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
        PaymentAttempt.objects.filter(order=order).update(expires_at=timezone.now()-timedelta(seconds=1))
        expire_pending_orders(); sync_payment(order.pk)
        order.refresh_from_db(); self.product.refresh_from_db()
        self.assertEqual(order.status, 'pending_payment'); self.assertEqual(self.product.stock, 4)
        self.gateway.close_payment.assert_not_called()
        with self.assertRaises(BusinessError): cancel_order(order.pk, self.student, '')

    def test_expired_known_unpaid_attempt_closed_then_releases_no_offline_delivery(self):
        order = self.create(); start_payment(order.pk, self.student, 'native', '127.0.0.1')
        Order.objects.filter(pk=order.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
        PaymentAttempt.objects.filter(order=order).update(expires_at=timezone.now()-timedelta(seconds=1))
        sync_payment(order.pk); expire_pending_orders()
        order.refresh_from_db(); self.product.refresh_from_db()
        self.assertEqual((order.status, order.payment_method), ('cancelled', 'wechat'))
        self.assertEqual(self.product.stock, 5); self.gateway.close_payment.assert_called_once()

    def test_user_pending_cancel_queues_full_refund_and_restock_once(self):
        order = self.create(); self.pay(order)
        cancel_order(order.pk, self.student, '无需配送'); cancel_order(order.pk, self.student, '重试')
        order.refresh_from_db(); self.assertEqual((order.status, order.payment_status), ('cancelled', 'refunding'))
        refund = self.finish_refund(order)
        self.assertEqual(refund.amount_cents, 1100); self.assertEqual(order.payment_status, 'refunded')
        self.assertEqual(self.product.stock, 5); self.gateway.refund.assert_called_once()

    def test_merchant_rejection_and_timeout_queues_system_compensation(self):
        rejected = self.create(); self.pay(rejected)
        merchant_action(rejected.pk, self.vendor, 'reject')
        rejected.refresh_from_db(); self.assertEqual((rejected.status, rejected.payment_status), ('rejected', 'refunding'))
        expired = self.create(); self.pay(expired)
        Order.objects.filter(pk=expired.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
        expire_pending_orders(); expired.refresh_from_db()
        self.assertEqual((expired.status, expired.payment_status), ('cancelled', 'refunding'))
        self.assertIsNone(PaymentRefund.objects.get(order=expired).requested_by)
        self.assertEqual(PaymentRefund.objects.count(), 2)

    def test_accepted_cancel_waits_for_merchant_and_does_not_restock_prepared_food(self):
        order = self.create(); self.pay(order); merchant_action(order.pk, self.vendor, 'accept')
        cancel_order(order.pk, self.student, '申请取消')
        order.refresh_from_db(); self.assertTrue(order.cancel_requested); self.assertEqual(order.payment_status, 'paid')
        with self.assertRaises(BusinessError): merchant_action(order.pk, self.vendor, 'ready')
        merchant_action(order.pk, self.vendor, 'approve_cancel')
        self.finish_refund(order); self.assertEqual(self.product.stock, 4)

    def test_refund_configuration_outage_is_durable_and_worker_retries_same_number(self):
        order = self.create(); self.pay(order); cancel_order(order.pk, self.student, '')
        refund = PaymentRefund.objects.get(order=order)
        with patch('market.payments.client_for', side_effect=GatewayError('CONFIG_MISSING', '配置暂不可用')):
            sync_payment(order.pk)
        refund.refresh_from_db(); order.refresh_from_db()
        self.assertEqual(refund.status, 'reconcile'); self.assertEqual(order.payment_status, 'refunding')
        call_command('reconcile_payments', verbosity=0)
        self.assertEqual(PaymentRefund.objects.get(order=order).out_refund_no, refund.out_refund_no)
        self.gateway.refund.assert_called_once()

    def test_closed_refund_cannot_resume_delivery_or_claim_refunded(self):
        order = self.create(); self.to_arrived(order)
        request_refund(order.pk, self.vendor, '配送异常退款')
        self.gateway.query_refund.side_effect = lambda number: refund_result(PaymentRefund.objects.get(out_refund_no=number), 'CLOSED')
        sync_payment(order.pk); order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status), ('cancelled', 'paid'))
        with self.assertRaises(BusinessError): confirm_receipt(order.pk, self.student)
        with self.assertRaises(BusinessError): merchant_action(order.pk, self.vendor, 'complete', order.pickup_code)
        self.assertEqual(self.api.delete('/api/v1/auth/account', {'password': 'DemoStrong123'}, format='json').status_code, 409)

    def test_payment_after_point_shutdown_queues_refund_without_acceptance(self):
        order = self.create(); start_payment(order.pk, self.student, 'native', '127.0.0.1')
        self.point.is_active = False; self.point.save()
        self.notify(PaymentAttempt.objects.get(order=order)); order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status), ('cancelled', 'refunding'))
        self.assertTrue(order.inventory_released)

    def test_service_stopped_during_gateway_query_is_rechecked_before_acceptance(self):
        order = self.create(); start_payment(order.pk, self.student, 'native', '127.0.0.1')
        def result(number):
            type(self.stall).objects.filter(pk=self.stall.pk).update(delivery_enabled=False)
            return payment_result(PaymentAttempt.objects.get(out_trade_no=number))
        self.gateway.query_payment.side_effect = result
        sync_payment(order.pk); order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status), ('cancelled', 'refunding'))

    def test_late_processing_refund_query_cannot_undo_closed_notification(self):
        order = self.create(); self.pay(order); cancel_order(order.pk, self.student, '')
        sync_payment(order.pk)
        def query(number):
            refund = PaymentRefund.objects.get(out_refund_no=number)
            old_result = refund_result(refund, 'PROCESSING')
            self.gateway.verify_notification.return_value = {'event_type': 'REFUND.CLOSED', 'resource': refund_result(refund, 'CLOSED', notification=True)}
            handle_notification('own-account', {}, b'{}')
            return old_result
        self.gateway.query_refund.side_effect = query
        sync_payment(order.pk); order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status), ('cancelled', 'paid'))
        self.assertEqual(PaymentRefund.objects.get(order=order).status, 'closed')
        self.gateway.refund.assert_called_once()

    def test_abnormal_refund_does_not_regress_or_starve_worker_after_old_processing(self):
        order = self.create(); self.pay(order); cancel_order(order.pk, self.student, '')
        sync_payment(order.pk)
        refund = PaymentRefund.objects.get(order=order)
        old_checked = timezone.now()-timedelta(days=1)
        PaymentRefund.objects.filter(pk=refund.pk).update(status='abnormal', last_checked_at=old_checked)
        self.gateway.query_refund.side_effect = lambda number: refund_result(PaymentRefund.objects.get(out_refund_no=number), 'PROCESSING')
        sync_payment(order.pk); refund.refresh_from_db()
        self.assertEqual(refund.status, 'abnormal'); self.assertGreater(refund.last_checked_at, old_checked)

    def test_external_refund_fact_keeps_manual_review_gate_after_prepaid(self):
        order = self.create(); self.pay(order)
        self.gateway.query_payment.side_effect = lambda number: payment_result(PaymentAttempt.objects.get(out_trade_no=number), 'REFUND')
        sync_payment(order.pk); order.refresh_from_db()
        self.assertTrue(order.payment_review_required)
        with self.assertRaises(BusinessError): merchant_action(order.pk, self.vendor, 'accept')

    def test_active_summary_and_account_deactivation_include_delivery_states(self):
        order = self.create()
        for status in ('pending_payment', 'delivering', 'arrived'):
            Order.objects.filter(pk=order.pk).update(status=status)
            response = self.api.get('/api/v1/orders/active-summary')
            self.assertEqual(response.data['counts'][status], 1)
            self.assertEqual(response.data['order']['fulfillment_type'], 'delivery')
            self.assertEqual(self.api.delete('/api/v1/auth/account', {'password': 'DemoStrong123'}, format='json').status_code, 409)

    def test_delivery_issue_blocks_fulfilment_until_audited_resolution(self):
        order = self.create(); self.pay(order)
        for action in ('accept', 'ready'): merchant_action(order.pk, self.vendor, action)
        merchant_action(order.pk, self.vendor, 'report_delivery_issue', reason='包装损坏，正在更换')
        with self.assertRaises(BusinessError): merchant_action(order.pk, self.vendor, 'dispatch')
        with self.assertRaises(BusinessError): merchant_action(order.pk, self.vendor, 'resolve_delivery_issue')
        merchant_action(order.pk, self.vendor, 'resolve_delivery_issue', reason='已更换密封包装')
        for action in ('dispatch', 'arrive'): merchant_action(order.pk, self.vendor, action)
        merchant_action(order.pk, self.vendor, 'report_delivery_issue', reason='暂时无法联系收餐人')
        with self.assertRaises(BusinessError): confirm_receipt(order.pk, self.student)
        with self.assertRaises(BusinessError): merchant_action(order.pk, self.vendor, 'complete', order.pickup_code)
        merchant_action(order.pk, self.vendor, 'resolve_delivery_issue', reason='已联系用户到场')
        self.assertEqual(confirm_receipt(order.pk, self.student).status, 'completed')


@skipUnless(connection.vendor == 'postgresql', 'Delivery race tests require PostgreSQL row locks.')
class DeliveryConcurrencyTests(DeliverySetup, TransactionTestCase):
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
            return [f.result(timeout=30) for f in futures]

    def test_last_delivery_capacity_reserved_once(self):
        self.stall.delivery_capacity = 1; self.stall.save()
        left, right = self.data(), self.data()
        self.race(lambda: create_order(self.student, left), lambda: create_order(self.other, right))
        self.assertEqual(Order.objects.count(), 1)
        self.product.refresh_from_db(); self.assertEqual(self.product.stock, 4)

    def test_gateway_operations_run_outside_database_transactions(self):
        order = self.create()
        def create(**kwargs):
            self.assertFalse(connection.in_atomic_block)
            return {'code_url': 'weixin://wxpay/test-code'}
        self.gateway.create_payment.side_effect = create
        self.pay(order); cancel_order(order.pk, self.student, '')
        def refund(**kwargs):
            self.assertFalse(connection.in_atomic_block)
            return refund_result(PaymentRefund.objects.get(out_refund_no=kwargs['out_refund_no']), 'PROCESSING')
        self.gateway.refund.side_effect = refund
        sync_payment(order.pk)

    def test_callback_can_win_during_payment_http_without_entry_overwriting_paid(self):
        order = self.create()
        def create(**kwargs):
            self.notify(PaymentAttempt.objects.get(out_trade_no=kwargs['out_trade_no']))
            return {'code_url': 'weixin://wxpay/test-code'}
        self.gateway.create_payment.side_effect = create
        start_payment(order.pk, self.student, 'native', '127.0.0.1')
        order.refresh_from_db(); payment = PaymentAttempt.objects.get(order=order)
        self.assertEqual((order.status, order.payment_status, payment.status), ('pending', 'paid', 'paid'))
        self.assertEqual(payment.code_url, '')

    def test_payment_success_racing_cancel_never_loses_paid_money(self):
        order = self.create(); start_payment(order.pk, self.student, 'native', '127.0.0.1')
        payment = PaymentAttempt.objects.get(order=order)
        self.race(lambda: self.notify(payment), lambda: cancel_order(order.pk, self.student, '取消'))
        order.refresh_from_db()
        self.assertIn((order.status, order.payment_status), [('pending', 'paid'), ('cancelled', 'refunding')])
        self.assertEqual(PaymentAttempt.objects.get(pk=payment.pk).status, 'paid')

    def test_verified_payment_racing_timeout_starts_fresh_acceptance_clock(self):
        order = self.create(); start_payment(order.pk, self.student, 'native', '127.0.0.1')
        payment = PaymentAttempt.objects.get(order=order)
        Order.objects.filter(pk=order.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.race(lambda: self.notify(payment), expire_pending_orders)
        order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status), ('pending', 'paid'))
        self.assertFalse(order.inventory_released)

    def test_prepaid_accept_and_cancel_cannot_both_release_prepared_inventory(self):
        order = self.create(); self.pay(order)
        self.race(lambda: merchant_action(order.pk, self.vendor, 'accept'), lambda: cancel_order(order.pk, self.student, '取消'))
        order.refresh_from_db(); self.product.refresh_from_db()
        if order.status == 'cancelled':
            self.assertEqual((order.payment_status, self.product.stock), ('refunding', 5))
        else:
            self.assertEqual((order.status, order.cancel_requested, self.product.stock), ('preparing', True, 4))

    def test_receipt_and_approved_cancellation_cannot_double_complete(self):
        order = self.create(); self.to_arrived(order); cancel_order(order.pk, self.student, '取消')
        self.race(lambda: confirm_receipt(order.pk, self.student), lambda: merchant_action(order.pk, self.vendor, 'approve_cancel'))
        order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status), ('cancelled', 'refunding'))
        self.assertIsNone(order.completed_at)
