"""Query pacing and asynchronous receipts use isolated DBs and gateway doubles."""
import io
import json
import statistics
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event, Thread
from unittest import skipUnless
from unittest.mock import patch
from urllib.request import Request, urlopen
from wsgiref.simple_server import WSGIRequestHandler, make_server

from django.contrib.auth.models import Permission, User
from django.core.management import call_command
from django.db import OperationalError, close_old_connections, connection, connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from .errors import BusinessError
from .models import AuditLog, Order, PaymentAttempt, PaymentNotification, PaymentRefund
from .notification_inbox import apply_claimed_notification, claim_notification, process_notifications
from .payments import close_payment, handle_notification, request_refund, start_payment, sync_payment
from .reconciliation import _observe
from .test_payments import PaymentSetup, payment_result, refund_result
from .test_simulation import SimulationSetup
from .wechatpay import GatewayError


class HardeningSetup(PaymentSetup):
    def setUp(self):
        super().setUp()
        pacing = override_settings(PAYMENT_QUERY_INTERVAL_SECONDS=5, PAYMENT_QUERY_FAILURE_DELAYS=(10, 20, 30, 60))
        pacing.enable(); self.addCleanup(pacing.disable)

    def notification(self, payment=None, **changes):
        payment = payment or self.start()
        self.gateway.verify_notification.return_value = {
            'id': uuid.uuid4().hex, 'event_type': 'TRANSACTION.SUCCESS',
            'resource': payment_result(payment, **changes),
        }
        return payment

    def enqueue(self):
        return handle_notification('own-account', {}, b'{}')


class PaymentPacingTests(HardeningSetup, TestCase):
    def test_sync_recover_and_worker_share_one_five_second_budget(self):
        payment = self.start()
        now = timezone.now()
        with patch('django.utils.timezone.now', return_value=now):
            sync_payment(self.order.pk, self.student)
            sync_payment(self.order.pk)
            start_payment(self.order.pk, self.student, 'native', '127.0.0.1')
            call_command('reconcile_payments', stdout=io.StringIO())
        self.assertEqual(self.gateway.query_payment.call_count, 1)
        payment.refresh_from_db()
        self.assertEqual(payment.next_query_at, now + timedelta(seconds=5))
        with patch('django.utils.timezone.now', return_value=now + timedelta(seconds=5)):
            sync_payment(self.order.pk, self.student)
        self.assertEqual(self.gateway.query_payment.call_count, 2)

    def test_error_backoff_grows_and_verified_result_resets_it(self):
        payment = self.start()
        self.gateway.query_payment.side_effect = GatewayError('NETWORK_UNKNOWN', '待确认', retryable=True)
        now = timezone.now()
        for delay in (10, 20, 30, 60, 60):
            with patch('django.utils.timezone.now', return_value=now):
                sync_payment(self.order.pk)
                sync_payment(self.order.pk)
            payment.refresh_from_db()
            self.assertEqual(payment.next_query_at, now + timedelta(seconds=delay))
            now = payment.next_query_at
        self.assertEqual(self.gateway.query_payment.call_count, 5)
        self.gateway.query_payment.side_effect = lambda number: payment_result(payment, 'NOTPAY')
        with patch('django.utils.timezone.now', return_value=now):
            sync_payment(self.order.pk)
        payment.refresh_from_db()
        self.assertEqual(payment.consecutive_query_failures, 0)
        self.assertEqual(payment.next_query_at, now + timedelta(seconds=5))

    def test_stale_lease_cannot_overwrite_new_query_schedule(self):
        payment = self.start()
        future = timezone.now() + timedelta(minutes=3)
        def superseded(number):
            PaymentAttempt.objects.filter(pk=payment.pk).update(request_in_flight_until=future,
                next_query_at=future, consecutive_query_failures=4)
            raise GatewayError('OLD_FAILURE', '旧请求失败')
        self.gateway.query_payment.side_effect = superseded
        sync_payment(self.order.pk)
        payment.refresh_from_db()
        self.assertEqual((payment.next_query_at, payment.consecutive_query_failures), (future, 4))

    def test_close_ignores_query_cooldown_only_for_closable_unpaid_intent(self):
        self.start()
        sync_payment(self.order.pk)
        close_payment(self.order.pk, self.student)
        self.gateway.close_payment.assert_called_once()
        self.assertEqual(self.gateway.query_payment.call_count, 2)
        close_payment(self.order.pk, self.student)
        self.assertEqual(self.gateway.query_payment.call_count, 2)

    def test_paid_order_cannot_bypass_query_pacing_through_close(self):
        self.pay()
        close_payment(self.order.pk, self.student)
        self.gateway.query_payment.assert_not_called()
        self.gateway.close_payment.assert_not_called()

    def test_refund_clicks_and_sync_share_query_budget(self):
        self.pay()
        request_refund(self.order.pk, self.vendor, '取消取餐')
        request_refund(self.order.pk, self.vendor, '取消取餐')
        sync_payment(self.order.pk)
        self.gateway.query_refund.assert_called_once()
        self.gateway.refund.assert_called_once()
        self.assertIsNotNone(PaymentRefund.objects.get(order=self.order).next_query_at)

    def test_operator_observation_cannot_skip_shared_cooldown(self):
        payment = self.start()
        sync_payment(self.order.pk)
        operator = User.objects.create_user('pacing-operator', is_staff=True)
        operator.user_permissions.add(Permission.objects.get(codename='resolve_payments'))
        with self.assertRaises(BusinessError) as caught:
            _observe(self.order.pk, payment.pk, operator, '人工核验')
        self.assertEqual(caught.exception.detail['code'], 'payment_query_cooldown')
        self.assertEqual(self.gateway.query_payment.call_count, 1)


@override_settings(SERVICES_SIMULATION_ENABLED=True, DEMO_MODE=True, PRODUCTION=False)
class SimulationPacingTests(SimulationSetup, TestCase):
    def setUp(self):
        super().setUp()
        pacing = override_settings(PAYMENT_QUERY_INTERVAL_SECONDS=5, PAYMENT_QUERY_FAILURE_DELAYS=(10, 20, 30, 60))
        pacing.enable(); self.addCleanup(pacing.disable)

    def test_explicit_simulated_success_refreshes_pending_result_immediately(self):
        from .simulation import simulate_payment
        order = self.create(); payment = self.start(order)
        simulate_payment(order.pk, self.student, payment.pk, 'pending')
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'reconcile')
        self.assertGreater(payment.next_query_at, timezone.now())
        response = self.api.post(f'/api/v1/orders/{order.pk}/payments/simulate',
            {'payment_id': str(payment.pk), 'outcome': 'success'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['payment_status'], 'paid')
        self.assertEqual(response.data['status'], 'pending')
        self.live.assert_not_called()

    def test_explicit_refund_success_refreshes_failed_result_without_waiting(self):
        order = self.create(); self.pay(order)
        request_refund(order.pk, self.vendor, '模拟取消', simulation_outcome='failure')
        refund = PaymentRefund.objects.get(order=order)
        self.assertEqual(refund.status, 'abnormal')
        self.assertGreater(refund.next_query_at, timezone.now())
        self.api.force_authenticate(self.vendor)
        response = self.api.post(f'/api/v1/merchant/orders/{order.pk}/refunds/simulate',
            {'refund_id': str(refund.pk), 'outcome': 'success'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['refund']['status'], 'success')
        self.live.assert_not_called()


class NotificationInboxTests(HardeningSetup, TestCase):
    def test_callback_acknowledges_durable_receipt_before_business_application(self):
        self.notification(payer={'openid': 'must-not-persist'}, attach='private-free-text')
        url = '/api/v1/payments/wechat/notify/own-account'
        self.assertEqual(self.api.post(url, '{}', content_type='application/json').status_code, 204)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, 'unpaid')
        row = PaymentNotification.objects.get()
        self.assertNotIn('payer', row.resource)
        self.assertNotIn('attach', row.resource)
        with patch('market.payments.client_for', side_effect=AssertionError('worker must not reload or reverify signing keys')):
            self.assertEqual(process_notifications(), (1, 0))
        self.order.refresh_from_db(); row.refresh_from_db()
        self.assertEqual((self.order.payment_status, row.status), ('paid', 'done'))

    def test_same_event_is_deduplicated_and_conflicting_facts_never_replace_it(self):
        self.notification()
        first = self.enqueue()
        self.assertEqual(first.pk, self.enqueue().pk)
        self.gateway.verify_notification.return_value['resource']['amount']['total'] += 1
        with self.assertRaises(GatewayError): self.enqueue()
        first.refresh_from_db()
        self.assertEqual(first.resource['amount']['total'], self.order.total_cents)
        self.assertEqual(first.conflict_count, 1)
        self.assertEqual(PaymentNotification.objects.count(), 1)
        self.assertEqual(process_notifications(), (1, 0))
        self.assertEqual(AuditLog.objects.filter(action='wechat_payment_confirmed').count(), 1)
        call_command('retry_payment_notification', str(first.pk), acknowledge_conflict=True,
            reason='已对账确认保留原始通知', stdout=io.StringIO())
        first.refresh_from_db()
        self.assertEqual((first.status, first.conflict_count), ('done', 0))
        self.assertEqual(AuditLog.objects.get(action='payment_notification_conflict_ack').details['conflict_count'], 1)

    def test_invalid_signature_never_creates_durable_work(self):
        self.gateway.verify_notification.side_effect = GatewayError('INVALID_SIGNATURE', '未验证')
        with self.assertRaises(GatewayError): self.enqueue()
        self.assertFalse(PaymentNotification.objects.exists())

    def test_amount_mismatch_is_preserved_as_dead_letter_without_payment_effect(self):
        self.notification(amount={'total': 1, 'currency': 'CNY'})
        row = self.enqueue()
        self.assertEqual(process_notifications(), (0, 1))
        row.refresh_from_db(); self.order.refresh_from_db()
        self.assertEqual((row.status, self.order.payment_status), ('dead', 'unpaid'))
        call_command('retry_payment_notification', str(row.pk), reason='核验原始记录后重试', stdout=io.StringIO())
        row.refresh_from_db()
        self.assertEqual((row.status, row.attempts), ('pending', 1))
        self.assertEqual(AuditLog.objects.filter(action='payment_notification_retry').count(), 1)

    def test_crash_after_claim_can_be_reclaimed_and_delivered_once(self):
        self.notification()
        row = self.enqueue()
        abandoned = claim_notification()
        self.assertIsNone(claim_notification())
        PaymentNotification.objects.filter(pk=row.pk).update(lease_until=timezone.now()-timedelta(seconds=1))
        replacement = claim_notification()
        self.assertFalse(apply_claimed_notification(abandoned))
        self.order.refresh_from_db(); self.assertEqual(self.order.payment_status, 'unpaid')
        self.assertTrue(apply_claimed_notification(replacement))
        self.assertEqual(AuditLog.objects.filter(action='wechat_payment_confirmed').count(), 1)

    def test_done_marker_and_financial_effect_rollback_together_then_retry(self):
        self.notification()
        row = self.enqueue()
        original_save = PaymentNotification.save
        def crash(instance, *args, **kwargs):
            if instance.status == 'done': raise RuntimeError('crash before commit')
            return original_save(instance, *args, **kwargs)
        with patch.object(PaymentNotification, 'save', crash):
            self.assertEqual(process_notifications(), (0, 1))
        row.refresh_from_db(); self.order.refresh_from_db()
        self.assertEqual((row.status, self.order.payment_status), ('retry', 'unpaid'))
        self.assertFalse(AuditLog.objects.filter(action='wechat_payment_confirmed').exists())
        PaymentNotification.objects.filter(pk=row.pk).update(available_at=timezone.now())
        self.assertEqual(process_notifications(), (1, 0))
        self.assertEqual(AuditLog.objects.filter(action='wechat_payment_confirmed').count(), 1)

    def test_database_failure_and_exhausted_processing_slots_return_retryable_503(self):
        from .payment_views import _notification_slots
        url = '/api/v1/payments/wechat/notify/own-account'
        with patch('market.payments.handle_notification', side_effect=OperationalError('no database')):
            response = self.api.post(url, '{}', content_type='application/json')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response['Retry-After'], '1')
        _notification_slots.acquire(); _notification_slots.acquire()
        try:
            self.assertEqual(self.api.post(url, '{}', content_type='application/json').status_code, 503)
        finally:
            _notification_slots.release(); _notification_slots.release()

    def test_dedicated_wsgi_exposes_only_bounded_callback_and_local_health(self):
        from config.callback_wsgi import application
        statuses = []
        def response(status, headers): statuses.append(status)
        with patch('config.callback_wsgi._django_application') as django_app:
            self.assertEqual(application({'REQUEST_METHOD': 'GET', 'PATH_INFO': '/callback-health'}, response), [b'{"status":"ok"}'])
            application({'REQUEST_METHOD': 'GET', 'PATH_INFO': '/api/v1/orders'}, response)
            application({'REQUEST_METHOD': 'POST', 'PATH_INFO': '/api/v1/payments/wechat/notify/own-account',
                'CONTENT_LENGTH': str(2*1024*1024+1)}, response)
            django_app.assert_not_called()
        self.assertEqual([status[:3] for status in statuses], ['200', '404', '413'])


@skipUnless(connection.vendor == 'postgresql', 'Requires real row locks')
class PaymentHardeningConcurrencyTests(HardeningSetup, TransactionTestCase):
    def test_notification_receipt_does_not_wait_for_order_lock(self):
        self.notification()
        locked, release = Event(), Event()
        def hold():
            close_old_connections()
            try:
                with transaction.atomic():
                    Order.objects.select_for_update().get(pk=self.order.pk)
                    locked.set()
                    if not release.wait(10): raise AssertionError('receipt waited for the order')
            finally: connections.close_all()
        with ThreadPoolExecutor(max_workers=1) as pool:
            task = pool.submit(hold)
            try:
                self.assertTrue(locked.wait(5))
                self.assertIsNotNone(self.enqueue().pk)
            finally: release.set()
            task.result(10)

    def test_parallel_query_and_worker_call_provider_once(self):
        self.start()
        barrier = Barrier(2)
        def run():
            close_old_connections()
            try:
                barrier.wait(5)
                sync_payment(self.order.pk)
            finally: connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run) for _ in range(2)]
            for future in futures: future.result(10)
        self.gateway.query_payment.assert_called_once()

    def test_parallel_claims_only_apply_each_event_once(self):
        self.notification(); self.enqueue()
        barrier = Barrier(2)
        def run():
            close_old_connections()
            try:
                barrier.wait(5)
                return process_notifications()
            finally: connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run) for _ in range(2)]
            totals = [future.result(10) for future in futures]
        self.assertEqual(sum(value[0] for value in totals), 1)
        self.assertEqual(AuditLog.objects.filter(action='wechat_payment_confirmed').count(), 1)


@skipUnless(connection.vendor == 'postgresql', 'Ingestion timing is measured on an isolated PostgreSQL DB')
@override_settings(ALLOWED_HOSTS=['127.0.0.1', 'testserver'])
class NotificationIngressBenchmarkTests(HardeningSetup, TransactionTestCase):
    def test_mock_signature_loopback_duplicate_ingress_latency(self):
        """A sequential, local receipt benchmark; not a production/real-signature SLA."""
        from config.callback_wsgi import application
        self.notification()
        class QuietHandler(WSGIRequestHandler):
            def log_message(self, *args): pass
        server = make_server('127.0.0.1', 0, application, handler_class=QuietHandler)
        def serve():
            try: server.serve_forever(poll_interval=0.02)
            finally: connections.close_all()
        thread = Thread(target=serve, daemon=True)
        thread.start()
        durations = []
        url = f'http://127.0.0.1:{server.server_port}/api/v1/payments/wechat/notify/own-account'
        body = json.dumps({'fixture': 'x' * 1024}).encode()
        try:
            for _ in range(100):
                started = time.perf_counter()
                with urlopen(Request(url, data=body, headers={'Content-Type': 'application/json'}), timeout=5) as response:
                    self.assertEqual(response.status, 204)
                    response.read()
                durations.append((time.perf_counter() - started) * 1000)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(PaymentNotification.objects.count(), 1)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, 'unpaid')
        print('notification_ingress_benchmark=' + json.dumps({
            'transport': 'loopback HTTP / wsgiref', 'database': 'isolated PostgreSQL',
            'signature': 'mocked; excludes cryptographic verification', 'load': '100 sequential duplicate notifications',
            'body_bytes': len(body), 'p50_ms': round(statistics.median(durations), 3),
            'p95_ms': round(sorted(durations)[94], 3), 'max_ms': round(max(durations), 3),
            'durable_rows': 1, 'business_state_before_worker': 'unpaid',
        }, ensure_ascii=False))
