"""Executable rehearsal uses persisted state, and never substitutes a fake for live WeChat."""
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import time, timedelta
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from django.conf import settings
from django.core.management import call_command
from django.db import close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from .delivery import delivery_settings
from .errors import BusinessError
from .models import DeliveryPoint, Order, PaymentAttempt, PaymentRefund
from .payments import start_payment, sync_payment, close_payment, request_refund, handle_notification
from .services import create_order, cancel_order, merchant_action, confirm_receipt, expire_pending_orders
from .simulation import prepare_stall, simulate_payment, simulate_refund
from .tests import fixtures, payload
from .wechatpay import GatewayError


class SimulationSetup:
    def setUp(self):
        pacing = override_settings(PAYMENT_QUERY_INTERVAL_SECONDS=0, PAYMENT_QUERY_FAILURE_DELAYS=(0,),
            CHECKOUT_MAX_ACTIVE_PER_STALL=100, CHECKOUT_MAX_ACTIVE_TOTAL=100)
        pacing.enable(); self.addCleanup(pacing.disable)
        self.student, self.other, self.vendor, self.stall, self.product = fixtures()
        self.stall.is_demo = True
        self.stall.simulation_payment_enabled = self.stall.simulation_delivery_enabled = True
        self.stall.save()
        self.point = prepare_stall(self.stall)
        self.api = APIClient(); self.api.force_authenticate(self.student)
        gateway = patch('market.payments.client_for', side_effect=AssertionError('simulation reached live gateway'))
        self.live = gateway.start(); self.addCleanup(gateway.stop)

    def create(self, delivery=True, **changes):
        data = payload(self.stall, self.product)
        if delivery:
            data.update(fulfillment_type='delivery', delivery_point_id=self.point.pk, expected_delivery_fee_cents=200,
                recipient_name='演练同学', contact_phone='13800000000')
        data.update(changes)
        return create_order(self.student, data)[0]

    def start(self, order):
        start_payment(order.pk, self.student, 'simulation', '127.0.0.1')
        return order.payments.first()

    def pay(self, order):
        payment = self.start(order)
        simulate_payment(order.pk, self.student, payment.pk, 'success')
        order.refresh_from_db()
        return payment


@override_settings(SERVICES_SIMULATION_ENABLED=True, DEMO_MODE=True, PRODUCTION=False)
class SimulationTests(SimulationSetup, TestCase):
    def test_delivery_full_flow_requires_actual_receipt(self):
        order = self.create()
        self.assertEqual(order.mode, 'simulation')
        self.assertEqual(order.total_cents, 1000)
        self.assertEqual(order.status, 'pending_payment')
        with self.assertRaises(BusinessError): merchant_action(order.pk, self.vendor, 'accept')
        payment = self.pay(order)
        self.assertEqual(order.status, 'pending')
        self.assertEqual(order.payment_status, 'paid')
        for action in ('accept', 'ready', 'dispatch', 'arrive'):
            merchant_action(order.pk, self.vendor, action)
        order.refresh_from_db()
        self.assertEqual(order.status, 'arrived'); self.assertIsNone(order.completed_at)
        confirm_receipt(order.pk, self.student)
        with self.assertRaises(BusinessError): confirm_receipt(order.pk, self.student)
        self.assertFalse(self.live.called)
        self.assertEqual(payment.mode, 'simulation')

    def test_pickup_payment_keeps_ready_gate_and_offline_option(self):
        order = self.create(delivery=False)
        with self.assertRaises(BusinessError): self.start(order)
        for action in ('accept', 'ready'): merchant_action(order.pk, self.vendor, action)
        payment = self.start(order)
        simulate_payment(order.pk, self.student, payment.pk, 'failure')
        order.refresh_from_db()
        self.assertEqual(order.payment_status, 'unpaid')
        self.assertEqual(order.payment_method, 'offline')
        merchant_action(order.pk, self.vendor, 'confirm_payment')
        merchant_action(order.pk, self.vendor, 'complete', order.pickup_code)

    def test_pending_result_does_not_collect_and_recovers_same_intent(self):
        order = self.create(); payment = self.start(order)
        result = simulate_payment(order.pk, self.student, payment.pk, 'pending')
        self.assertEqual(result.payment_status, 'unpaid')
        payment.refresh_from_db(); self.assertEqual(payment.status, 'reconcile')
        self.assertIn('待确认', payment.error_message)
        with self.assertRaises(BusinessError): cancel_order(order.pk, self.student, '取消')
        with self.assertRaises(BusinessError): merchant_action(order.pk, self.vendor, 'accept')
        self.start(order)
        self.assertEqual(order.payments.count(), 1)
        simulate_payment(order.pk, self.student, payment.pk, 'success')
        simulate_payment(order.pk, self.student, payment.pk, 'failure')
        order.refresh_from_db(); self.assertEqual(order.payment_status, 'paid')
        self.assertEqual(order.payments.count(), 1)

    def test_failure_releases_hold_not_inventory_until_cancel_and_old_id_cannot_pay(self):
        order = self.create(); first = self.start(order)
        simulate_payment(order.pk, self.student, first.pk, 'failure')
        self.product.refresh_from_db(); self.assertEqual(self.product.stock, 4)
        second = self.start(order)
        self.assertNotEqual(first.pk, second.pk)
        with self.assertRaises(BusinessError): simulate_payment(order.pk, self.student, first.pk, 'success')
        simulate_payment(order.pk, self.student, second.pk, 'failure')
        cancel_order(order.pk, self.student, '取消')
        cancel_order(order.pk, self.student, '取消')
        self.product.refresh_from_db(); self.assertEqual(self.product.stock, 5)

    def test_refund_failure_pending_and_success_are_durable_and_idempotent(self):
        order = self.create(); self.pay(order)
        request_refund(order.pk, self.vendor, '模拟售后', 'failure')
        refund = PaymentRefund.objects.get(order=order)
        self.assertEqual((refund.mode, refund.status), ('simulation', 'abnormal'))
        sync_payment(order.pk)
        refund.refresh_from_db(); self.assertEqual(refund.status, 'abnormal')
        simulate_refund(order.pk, self.vendor, refund.pk, 'success')
        simulate_refund(order.pk, self.vendor, refund.pk, 'failure')
        refund.refresh_from_db(); order.refresh_from_db()
        self.assertEqual(refund.status, 'success')
        self.assertEqual(order.payment_status, 'refunded')
        self.assertEqual(PaymentRefund.objects.count(), 1)

    def test_pending_refund_stays_pending_until_explicit_resolution(self):
        order = self.create(); self.pay(order)
        request_refund(order.pk, self.vendor, '模拟待处理', 'pending')
        refund = PaymentRefund.objects.get(order=order)
        sync_payment(order.pk)
        refund.refresh_from_db(); self.assertEqual(refund.status, 'processing')
        simulate_refund(order.pk, self.vendor, refund.pk, 'success')
        order.refresh_from_db(); self.assertEqual(order.payment_status, 'refunded')

    def test_user_cancel_and_timeout_refund_full_fee_restore_inventory_once(self):
        order = self.create(); self.pay(order)
        order.expires_at = timezone.now()-timedelta(seconds=1); order.save()
        expire_pending_orders(); expire_pending_orders(); sync_payment(order.pk)
        order.refresh_from_db(); self.product.refresh_from_db()
        self.assertEqual((order.status, order.payment_status), ('cancelled', 'refunded'))
        self.assertEqual(PaymentRefund.objects.get(order=order).amount_cents, 1000)
        self.assertEqual(self.product.stock, 5)

    def test_simulation_off_never_changes_provider_or_allows_real_fulfilment(self):
        order = self.create(); payment = self.start(order)
        with override_settings(SERVICES_SIMULATION_ENABLED=False):
            with self.assertRaises(BusinessError): simulate_payment(order.pk, self.student, payment.pk, 'success')
            with self.assertRaises(BusinessError): merchant_action(order.pk, self.vendor, 'accept')
            with self.assertRaises(BusinessError): cancel_order(order.pk, self.student, '')
            sync_payment(order.pk)
            response = self.api.get(f'/api/v1/orders/{order.pk}')
            self.assertEqual(response.data['mode'], 'simulation')
            self.assertEqual(response.data['wechat_payment']['mode'], 'simulation')
            self.assertFalse(response.data['wechat_payment']['available'])
        self.assertFalse(self.live.called)
        order.refresh_from_db(); self.assertEqual(order.payment_status, 'unpaid')

    def test_simulator_requires_owner_current_id_and_csrf(self):
        order = self.create(); payment = self.start(order)
        for user in (self.other, self.vendor):
            with self.assertRaises(BusinessError): simulate_payment(order.pk, user, payment.pk, 'success')
        self.pay(order)
        request_refund(order.pk, self.vendor, '模拟退款', 'pending')
        refund = PaymentRefund.objects.get(order=order)
        for user in (self.other, self.student):
            with self.assertRaises(BusinessError): simulate_refund(order.pk, user, refund.pk, 'success')
        secure = APIClient(enforce_csrf_checks=True); secure.force_login(self.student)
        result = secure.post(f'/api/v1/orders/{order.pk}/payments/simulate', {'payment_id': str(payment.pk), 'outcome': 'success'})
        self.assertEqual(result.status_code, 403)

    def test_live_order_does_not_accept_simulation_channel_or_outcome(self):
        self.stall.is_demo = False; self.stall.save()
        order = self.create(delivery=False)
        for action in ('accept', 'ready'): merchant_action(order.pk, self.vendor, action)
        with self.assertRaises(BusinessError): self.start(order)
        with self.assertRaises(BusinessError): request_refund(order.pk, self.vendor, '假的退款', 'success')
        self.assertFalse(self.live.called)
        self.assertEqual(order.mode, 'live')

    def test_real_notification_rejects_simulation_account_before_gateway(self):
        order = self.create(); payment = self.start(order)
        with self.assertRaises(GatewayError): handle_notification(payment.account_key, {}, b'{}')
        self.assertFalse(self.live.called)

    def test_shared_financial_machine_rejects_corrupted_refund_mode(self):
        order = self.create(); self.pay(order)
        request_refund(order.pk, self.vendor, '故障', 'pending')
        refund = PaymentRefund.objects.get(order=order)
        refund.mode = 'live'; refund.save()
        sync_payment(order.pk)
        refund.refresh_from_db(); self.assertEqual(refund.status, 'reconcile')
        self.assertFalse(self.live.called)

    def test_switches_are_simple_do_not_grant_real_approval_or_verify_location(self):
        self.api.force_authenticate(self.vendor)
        confirmed = self.stall.current_session.last_confirmed_at
        response = self.api.patch(f'/api/v1/merchant/stalls/{self.stall.pk}/services',
            {'online_payment_enabled': True, 'delivery_enabled': True}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['delivery']['available'])
        self.stall.refresh_from_db()
        self.assertFalse(self.stall.delivery_approved); self.assertFalse(self.stall.delivery_enabled)
        self.assertEqual(self.stall.current_session.last_confirmed_at, confirmed)
        self.assertFalse(self.point.is_active); self.assertTrue(self.point.is_simulation)
        response = self.api.patch(f'/api/v1/merchant/stalls/{self.stall.pk}/services', {'is_verified': True}, format='json')
        self.assertEqual(response.status_code, 400)
        self.api.force_authenticate(self.other)
        self.assertEqual(self.api.get(f'/api/v1/merchant/stalls/{self.stall.pk}/services').status_code, 404)

    def test_preparation_preserves_custom_settings_and_is_idempotent(self):
        self.stall.simulation_defaults_prepared = False
        self.stall.delivery_fee_cents = 777
        self.stall.delivery_starts_at = time(10)
        self.stall.save()
        call_command('prepare_simulation', enable_services=True)
        call_command('prepare_simulation', enable_services=True)
        self.stall.refresh_from_db()
        self.assertEqual(self.stall.delivery_fee_cents, 777)
        self.assertEqual(self.stall.delivery_starts_at, time(10))
        self.assertEqual(DeliveryPoint.objects.filter(is_simulation=True).count(), 1)

    def test_services_can_be_disabled_after_location_change_suspends_ordering(self):
        self.api.force_authenticate(self.vendor)
        endpoint = f'/api/v1/merchant/stalls/{self.stall.pk}'
        moved = self.api.post(endpoint + '/status', {'status': 'open', 'confirm_location': True,
            'address': '新的模拟出摊位置', 'latitude': 31.235, 'longitude': 121.478}, format='json')
        self.assertEqual(moved.status_code, 200, moved.data)
        self.assertFalse(moved.data['transaction_enabled'])
        response = self.api.patch(endpoint + '/services',
            {'online_payment_enabled': False, 'delivery_enabled': False}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.stall.refresh_from_db()
        self.assertFalse(self.stall.simulation_payment_enabled)
        self.assertFalse(self.stall.simulation_delivery_enabled)
        self.assertFalse(self.stall.transaction_enabled)
        self.assertFalse(response.data['wechat_payment']['available'])
        self.assertFalse(response.data['delivery']['available'])

    def test_revoked_qualification_allows_disabling_but_rejects_enabling_atomically(self):
        self.api.force_authenticate(self.vendor)
        self.stall.merchant.is_verified = False
        self.stall.merchant.save()
        endpoint = f'/api/v1/merchant/stalls/{self.stall.pk}/services'
        # A mixed request must not partially apply the disabling field.
        mixed = self.api.patch(endpoint, {'online_payment_enabled': False, 'delivery_enabled': True}, format='json')
        self.assertEqual(mixed.status_code, 409, mixed.data)
        self.stall.refresh_from_db()
        self.assertTrue(self.stall.simulation_payment_enabled)
        self.assertTrue(self.stall.simulation_delivery_enabled)
        disabled = self.api.patch(endpoint, {'online_payment_enabled': False, 'delivery_enabled': False}, format='json')
        self.assertEqual(disabled.status_code, 200, disabled.data)
        for field in ('online_payment_enabled', 'delivery_enabled'):
            with self.subTest(field=field):
                rejected = self.api.patch(endpoint, {field: True}, format='json')
                self.assertEqual(rejected.status_code, 409, rejected.data)
                self.assertEqual(rejected.data['code'], 'stall_unavailable')
                self.stall.refresh_from_db()
                self.assertFalse(self.stall.simulation_payment_enabled)
                self.assertFalse(self.stall.simulation_delivery_enabled)

    def test_services_disable_still_requires_owner_and_simulation_environment(self):
        endpoint = f'/api/v1/merchant/stalls/{self.stall.pk}/services'
        body = {'online_payment_enabled': False, 'delivery_enabled': False}
        self.api.force_authenticate(self.other)
        self.assertEqual(self.api.patch(endpoint, body, format='json').status_code, 404)
        self.api.force_authenticate(self.vendor)
        with override_settings(SERVICES_SIMULATION_ENABLED=False):
            rejected = self.api.patch(endpoint, body, format='json')
            self.assertEqual(rejected.status_code, 409, rejected.data)
            self.assertEqual(rejected.data['code'], 'simulation_unavailable')
        self.stall.refresh_from_db()
        self.assertTrue(self.stall.simulation_payment_enabled)
        self.assertTrue(self.stall.simulation_delivery_enabled)

    def test_live_points_cannot_be_used_for_simulation_or_simulation_points_for_live(self):
        real = DeliveryPoint.objects.create(area=self.stall.area, name='真实点', address='运营位置', latitude=31, longitude=121, is_active=True)
        self.stall.delivery_points.add(real)
        self.assertEqual([p['id'] for p in delivery_settings(self.stall)['points']], [self.point.pk])
        self.point.is_active = True; self.point.save()
        with override_settings(SERVICES_SIMULATION_ENABLED=False):
            self.assertEqual([p['id'] for p in delivery_settings(self.stall)['points']], [real.pk])

    def test_stale_price_inventory_and_capacity_gates_survive_simulation(self):
        with self.assertRaises(BusinessError): self.create(expected_delivery_fee_cents=999)
        self.stall.current_session.last_confirmed_at = timezone.now()-timedelta(hours=2)
        self.stall.current_session.save()
        with self.assertRaises(BusinessError): self.create()
        self.stall.current_session.last_confirmed_at = timezone.now(); self.stall.current_session.save()
        self.stall.delivery_capacity = 1; self.stall.save()
        self.create()
        with self.assertRaises(BusinessError): self.create()

    def test_metrics_separate_rehearsal_money_and_retain_mode_when_closed(self):
        order = self.create(); self.pay(order)
        self.api.force_authenticate(self.vendor)
        live = self.api.get('/api/v1/merchant/metrics').data
        simulated = self.api.get('/api/v1/merchant/metrics?mode=simulation').data
        self.assertEqual(live['revenue_cents'], 0)
        self.assertEqual(live['mode'], 'live')
        self.assertEqual(simulated['revenue_cents'], 1000)
        self.assertEqual(simulated['mode'], 'simulation')
        with override_settings(SERVICES_SIMULATION_ENABLED=False):
            self.assertEqual(self.api.get('/api/v1/merchant/metrics').data['revenue_cents'], 0)
            self.assertEqual(self.api.get('/api/v1/merchant/metrics?mode=simulation').data['revenue_cents'], 1000)

    def test_environment_rejects_simulation_in_production_and_outside_demo(self):
        for overrides in ({'DJANGO_ENV': 'production', 'DEMO_MODE': 'true'}, {'DJANGO_ENV': 'development', 'DEMO_MODE': 'false'}):
            result = subprocess.run([sys.executable, '-c', 'import config.settings'], cwd=settings.BASE_DIR,
                env={**os.environ, **overrides, 'SERVICES_SIMULATION_ENABLED': 'true'}, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('SERVICES_SIMULATION_ENABLED requires', result.stderr)

    def test_original_cash_demo_keeps_working_without_online_simulator(self):
        with override_settings(SERVICES_SIMULATION_ENABLED=False):
            order = self.create(delivery=False)
            self.assertEqual(order.mode, 'simulation')
            for action in ('accept', 'ready', 'confirm_payment'):
                merchant_action(order.pk, self.vendor, action)
            merchant_action(order.pk, self.vendor, 'complete', order.pickup_code)
            self.api.force_authenticate(self.vendor)
            self.assertEqual(self.api.get('/api/v1/merchant/metrics').data['revenue_cents'], 0)

    def test_paid_while_merchant_closes_refunds_instead_of_fulfilling(self):
        order = self.create(); payment = self.start(order)
        self.stall.current_session.status = 'closed'; self.stall.current_session.save()
        simulate_payment(order.pk, self.student, payment.pk, 'success')
        sync_payment(order.pk)
        order.refresh_from_db(); self.product.refresh_from_db()
        self.assertEqual((order.status, order.payment_status), ('cancelled', 'refunded'))
        self.assertEqual(self.product.stock, 5)


@override_settings(SERVICES_SIMULATION_ENABLED=True, DEMO_MODE=True, PRODUCTION=False)
@skipUnless(connection.vendor == 'postgresql', 'Row lock races require PostgreSQL')
class SimulationConcurrencyTests(SimulationSetup, TransactionTestCase):
    def race(self, *actions):
        barrier = Barrier(len(actions))
        def run(action):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try: return action()
                except BusinessError as error: return error.detail['code']
            finally: connections.close_all()
        with ThreadPoolExecutor(max_workers=len(actions)) as pool:
            return list(pool.map(run, actions))

    def test_double_simulated_success_collects_once(self):
        order = self.create(); payment = self.start(order)
        self.race(*(lambda: simulate_payment(order.pk, self.student, payment.pk, 'success') for _ in range(2)))
        order.refresh_from_db(); self.product.refresh_from_db()
        self.assertEqual((order.status, order.payment_status), ('pending', 'paid'))
        self.assertEqual(order.payments.count(), 1); self.assertEqual(self.product.stock, 4)

    def test_close_racing_simulated_success_never_loses_capture(self):
        order = self.create(); payment = self.start(order)
        self.race(lambda: simulate_payment(order.pk, self.student, payment.pk, 'success'),
            lambda: close_payment(order.pk, self.student))
        sync_payment(order.pk)
        payment.refresh_from_db(); order.refresh_from_db()
        if payment.simulation_state == 'SUCCESS':
            self.assertEqual(order.payment_status, 'paid'); self.assertEqual(payment.status, 'paid')
        else:
            self.assertEqual(payment.status, 'closed'); self.assertEqual(order.payment_status, 'unpaid')

    def test_refund_resolution_race_is_monotonic(self):
        order = self.create(); self.pay(order)
        request_refund(order.pk, self.vendor, '演练退款', 'pending')
        refund = PaymentRefund.objects.get(order=order)
        self.race(lambda: simulate_refund(order.pk, self.vendor, refund.pk, 'success'),
            lambda: simulate_refund(order.pk, self.vendor, refund.pk, 'failure'))
        # Success may race a pending provider query, but its persisted outcome must
        # never be overwritten by the later failure action once captured.
        sync_payment(order.pk)
        refund.refresh_from_db()
        self.assertEqual(refund.status, 'success')
