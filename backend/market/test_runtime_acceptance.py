"""PostgreSQL acceptance with bounded concurrency and real callback processes.

Run: python manage.py test market.test_runtime_acceptance --noinput --verbosity 2
The test runner creates/destroys its own database. No live gateway is contacted.
JSON result lines are intentionally separate for normal load, replay and faults.
"""
import base64
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
from http.client import HTTPConnection, HTTPException
import os
from pathlib import Path
import secrets
import socket
import statistics
import subprocess
import sys
import tempfile
from threading import Barrier, Lock
import time
from unittest import skipUnless
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.contrib.auth.models import User
from django.db import close_old_connections, connection, connections, transaction
from django.db.models import Sum
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from .errors import BusinessError
from .models import (AuditLog, BusinessSession, Order, OrderItem, PaymentAttempt,
                     PaymentNotification, PaymentRefund, Product, Stall, StallLocation)
from .services import cancel_order, create_order, merchant_action
from .test_payments import payment_result, refund_result
from .tests import fixtures, payload


def report(kind, **values):
    with connection.cursor() as cursor:
        cursor.execute('SHOW server_version')
        database_version = cursor.fetchone()[0]
    print('RUNTIME_' + kind + '_RESULTS ' + json.dumps({
        'schema_version': 1, 'database': 'PostgreSQL', 'database_version': database_version,
        'version': os.environ.get('RUNTIME_VERSION', 'working-tree'),
        'entry': 'python manage.py test market.test_runtime_acceptance --noinput --verbosity 2',
        'failures': [], **values,
    }, sort_keys=True), flush=True)


def parallel(count, workers, work):
    """Count actual in-flight work as well as the configured thread ceiling."""
    lock, barrier = Lock(), Barrier(workers)
    active = peak = 0
    def wrapped(index):
        nonlocal active, peak
        close_old_connections()
        try:
            if index < workers:
                barrier.wait(timeout=30)
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                return work(index)
            finally:
                with lock:
                    active -= 1
        finally:
            connections.close_all()
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(wrapped, range(count)))
    return results, peak, round(time.perf_counter() - started, 3)


@skipUnless(connection.vendor == 'postgresql', 'Runtime acceptance requires PostgreSQL row locks')
@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class RuntimeStockAcceptanceTests(TransactionTestCase):
    def setUp(self):
        self.student, self.other, self.vendor, self.stall, self.product = fixtures()

    def test_1000_different_users_compete_for_50_portions_at_20_concurrency(self):
        Product.objects.filter(pk=self.product.pk).update(stock=50)
        users = User.objects.bulk_create([User(username=f'contention-student-{i}', password='!') for i in range(1000)])
        def submit(index):
            try:
                order, created = create_order(users[index], payload(self.stall, self.product, key=f'isolated-stock-{index}'))
                self.assertTrue(created)
                return 'accepted'
            except BusinessError as exc:
                self.assertEqual(exc.detail['code'], 'out_of_stock')
                return exc.detail['code']
        results, peak, duration = parallel(1000, 20, submit)
        totals = Counter(results)
        self.assertEqual(totals, {'accepted': 50, 'out_of_stock': 950})
        self.assertEqual(Order.objects.count(), 50)
        self.assertEqual(OrderItem.objects.aggregate(total=Sum('quantity'))['total'], 50)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 0)
        self.assertEqual(self.product.stock_version, 50)
        self.assertFalse(Product.objects.filter(stock__lt=0).exists())
        report('STOCK', requests=1000, distinct_users=1000, concurrency=20,
               observed_peak_concurrency=peak, successful_portions=50, rejected=950,
               initial_stock=50, final_stock=0, stock_mutations=50, duration_seconds=duration)

    def test_same_key_concurrent_replays_reserve_exactly_once(self):
        request = payload(self.stall, self.product, key='same-concurrent-request')
        results, peak, duration = parallel(20, 20, lambda _: create_order(self.student, request))
        self.assertEqual(sum(created for _, created in results), 1)
        self.assertEqual(len({order.pk for order, _ in results}), 1)
        self.product.refresh_from_db()
        self.assertEqual((self.product.stock, self.product.stock_version), (4, 1))
        self.assertEqual(Order.objects.count(), 1)
        report('ORDER_REPLAY', requests=20, concurrency=20, observed_peak_concurrency=peak,
               created=1, replayed=19, stock_mutations=1, duration_seconds=duration)

    def test_cross_stall_concurrency_cancel_expiry_and_unknown_funds(self):
        pairs = [(self.stall, self.product)]
        for index in range(3):
            stall = Stall.objects.create(merchant=self.stall.merchant, area=self.stall.area,
                                        name=f'额度测试摊位{index}', transaction_enabled=True)
            StallLocation.objects.create(stall=stall, address='校园南门', latitude=31.23, longitude=121.47)
            stall.current_session = BusinessSession.objects.create(
                stall=stall, status='open', closes_at=timezone.now() + timedelta(hours=2))
            stall.save()
            pairs.append((stall, Product.objects.create(stall=stall, name='测试餐点', price_cents=800, stock=5)))
        def submit(index):
            try:
                order, _ = create_order(self.student, payload(*pairs[index]))
                return index, order.pk
            except BusinessError as exc:
                self.assertEqual(exc.detail['code'], 'active_reservation_limit')
                return index, None
        results, peak, duration = parallel(4, 4, submit)
        accepted = [(index, order_id) for index, order_id in results if order_id]
        rejected = next(index for index, order_id in results if not order_id)
        self.assertEqual(len(accepted), 3)
        self.assertEqual(sum(Product.objects.values_list('stock', flat=True)), 17)
        first_index, first_id = accepted[0]
        cancel_order(first_id, self.student, '隔离验收取消')
        self.assertEqual(Product.objects.get(pk=pairs[first_index][1].pk).stock, 5)
        replacement, _ = create_order(self.student, payload(*pairs[rejected]))
        second_index, second_id = accepted[1]
        Order.objects.filter(pk=second_id).update(expires_at=timezone.now() - timedelta(seconds=1))
        released, _ = create_order(self.student, payload(*pairs[first_index]))
        self.assertTrue(Order.objects.get(pk=second_id).inventory_released)
        self.assertEqual(Product.objects.get(pk=pairs[second_index][1].pk).stock, 5)
        # An expired payment with an unknown provider result still owns its slot.
        held = Order.objects.get(pk=accepted[2][1])
        PaymentAttempt.objects.create(order=held, merchant=self.stall.merchant,
            account_key='runtime-account', mchid='1900000109', appid='wxRuntimeAcceptance',
            out_trade_no='held-funds', channel='native', amount_cents=held.total_cents,
            expires_at=held.expires_at, status='reconcile')
        Order.objects.filter(pk=held.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        stock_before = dict(Product.objects.values_list('pk', 'stock'))
        with self.assertRaises(BusinessError) as caught:
            create_order(self.student, payload(*pairs[second_index]))
        self.assertEqual(caught.exception.detail['code'], 'active_reservation_limit')
        self.assertEqual(dict(Product.objects.values_list('pk', 'stock')), stock_before)
        self.assertFalse(Order.objects.get(pk=held.pk).inventory_released)
        # Already committed requests still replay after the account reaches its cap.
        request = {'stall_id': released.stall_id, 'items': [{'product_id': pairs[first_index][1].pk,
            'quantity': 1, 'expected_price_cents': 800}], 'note': '', 'contact_phone': '',
            'idempotency_key': released.idempotency_key}
        self.assertEqual(create_order(self.student, request)[0].pk, released.pk)
        report('GLOBAL_QUOTA', requests=4, concurrency=4, observed_peak_concurrency=peak,
               accepted=3, rejected=1, cancel_release=True, expiry_release=True,
               uncertain_payment_holds_slot=True, duration_seconds=duration)


@skipUnless(connection.vendor == 'postgresql', 'Runtime acceptance requires PostgreSQL row locks')
@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class RuntimeNotificationAcceptanceTests(TransactionTestCase):
    def setUp(self):
        self.student, self.other, self.vendor, self.stall, self.product = fixtures()
        Product.objects.filter(pk=self.product.pk).update(stock=200)
        self.directory = tempfile.TemporaryDirectory(prefix='yanhuo-runtime-notifications-')
        self.path = Path(self.directory.name)
        self.addCleanup(self.cleanup_directory)
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        merchant_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.aes_key = secrets.token_hex(16).encode()
        (self.path / 'merchant.pem').write_bytes(merchant_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        (self.path / 'provider.pem').write_bytes(self.key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
        (self.path / 'api-key').write_bytes(self.aes_key)
        accounts = {'accounts': {'runtime-account': {
            'merchant_profile_id': self.stall.merchant_id, 'enabled': False,
            'appid': 'wxRuntimeAcceptance', 'mchid': '1900000109', 'serial_no': 'ABC123',
            'private_key_path': 'merchant.pem', 'api_v3_key_file': 'api-key',
            'payment_public_keys': {'PUB_KEY_ID_123456': 'provider.pem'}, 'channels': ['native', 'h5'],
        }}}
        (self.path / 'accounts.json').write_text(json.dumps(accounts), encoding='utf-8')
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            self.port = probe.getsockname()[1]
        self.context = self.path / 'context.json'
        self.context.write_text(json.dumps({
            'database': connection.settings_dict, 'port': self.port,
            'accounts': str(self.path / 'accounts.json'), 'claim_marker': str(self.path / 'claimed.json'),
        }), encoding='utf-8')
        for item in self.path.iterdir():
            item.chmod(0o600)
        self.processes = []
        self.transport_errors = Counter()
        self.transport_error_lock = Lock()
        self.addCleanup(self.stop_processes)
        self.callback = self.start_process('callback')
        self.wait_for_callback()

    def start_process(self, mode):
        output = (self.path / f'{mode}-{len(self.processes)}.log').open('wb')
        # Windows venv python.exe is a redirector; launch the real interpreter
        # with this environment's package paths so kill() targets the actual
        # callback/worker, not a launcher that could leave its child behind.
        executable = sys._base_executable if os.name == 'nt' else sys.executable
        env = {**os.environ, 'PYTHONPATH': os.pathsep.join(sys.path), 'PYTHONUNBUFFERED': '1'}
        process = subprocess.Popen([executable, '-m', 'market.test_runtime_process', str(self.context), mode],
            cwd=Path(__file__).resolve().parent.parent, stdout=output, stderr=subprocess.STDOUT,
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        self.processes.append((process, output))
        return process

    def cleanup_directory(self):
        self.assertEqual(self.path.resolve().parent, Path(tempfile.gettempdir()).resolve())
        for attempt in range(30):
            try:
                self.directory.cleanup()
                return
            except PermissionError:
                if attempt == 29:
                    raise
                time.sleep(.1)

    def stop_processes(self):
        for process, output in self.processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
            output.close()
        # Keep diagnostics only; never export generated keys or database context.
        evidence = os.environ.get('RUNTIME_ACCEPTANCE_EVIDENCE_DIR')
        if evidence:
            destination = Path(evidence) / self._testMethodName
            destination.mkdir(parents=True, exist_ok=False)
            for source in self.path.glob('*.log'):
                (destination / source.name).write_bytes(source.read_bytes())

    def wait_for_callback(self):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.callback.poll() is not None:
                self.fail('Callback exited: ' + next(self.path.glob('callback-*.log')).read_text(errors='replace'))
            try:
                with build_opener(ProxyHandler({})).open(f'http://127.0.0.1:{self.port}/callback-health', timeout=1) as response:
                    if response.status == 200:
                        return
            except (URLError, TimeoutError):
                time.sleep(0.05)
        self.fail('Waitress callback did not become ready')

    def payment(self, index=0):
        user = User.objects.create(username=f'notification-student-{index}', password='!')
        order, _ = create_order(user, payload(self.stall, self.product))
        merchant_action(order.pk, self.vendor, 'accept')
        merchant_action(order.pk, self.vendor, 'ready')
        order.refresh_from_db()
        return PaymentAttempt.objects.create(order=order, merchant=self.stall.merchant,
            account_key='runtime-account', mchid='1900000109', appid='wxRuntimeAcceptance',
            out_trade_no=f'RUNTIME{index:08d}', channel='native', amount_cents=order.total_cents,
            expires_at=order.expires_at, status='pending')

    def envelope(self, resource, event_id, event_type='TRANSACTION.SUCCESS'):
        nonce, associated = secrets.token_hex(6).encode(), b'runtime-notification'
        encrypted = AESGCM(self.aes_key).encrypt(nonce, json.dumps(resource).encode(), associated)
        return json.dumps({'id': event_id, 'event_type': event_type, 'resource_type': 'encrypt-resource',
            'resource': {'algorithm': 'AEAD_AES_256_GCM', 'original_type': 'transaction',
                         'nonce': nonce.decode(), 'associated_data': associated.decode(),
                         'ciphertext': base64.b64encode(encrypted).decode()}}).encode()

    def send(self, body, *, invalid_signature=False):
        stamp, nonce = str(int(time.time())), secrets.token_hex(12)
        signature = self.key.sign(f'{stamp}\n{nonce}\n'.encode() + body + b'\n', padding.PKCS1v15(), hashes.SHA256())
        if invalid_signature:
            signature = b'invalid'
        request = Request(f'http://127.0.0.1:{self.port}/api/v1/payments/wechat/notify/runtime-account',
            data=body, headers={'Content-Type': 'application/json', 'Wechatpay-Timestamp': stamp,
                'Wechatpay-Nonce': nonce, 'Wechatpay-Serial': 'PUB_KEY_ID_123456',
                'Wechatpay-Signature': base64.b64encode(signature).decode()})
        started = time.perf_counter()
        try:
            with build_opener(ProxyHandler({})).open(request, timeout=10) as response:
                response.read()
                status = response.status
        except HTTPError as exc:
            status = exc.code
            exc.close()
        except (URLError, OSError, HTTPException) as exc:
            # Preserve one outcome for every attempted request. A connection
            # failure must not abort pool.map before it emits the cohort report.
            status = 0
            with self.transport_error_lock:
                self.transport_errors[type(exc).__name__] += 1
        return status, (time.perf_counter() - started) * 1000

    def await_status(self, event_id, status='done', timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            row = PaymentNotification.objects.filter(event_id=event_id).first()
            if row and row.status == status:
                return row
            time.sleep(0.05)
        self.fail(f'Notification {event_id} did not reach {status}')

    def oversized_request(self):
        # Send the declared length first: Waitress rejects before body buffering.
        # Sending megabytes after that rejection can mask its 413 with a TCP RST.
        client = HTTPConnection('127.0.0.1', self.port, timeout=5)
        started = time.perf_counter()
        try:
            client.putrequest('POST', '/api/v1/payments/wechat/notify/runtime-account')
            client.putheader('Content-Length', str(2097152 + 1))
            client.putheader('Content-Type', 'application/json')
            client.endheaders()
            response = client.getresponse()
            response.read()
            return response.status, (time.perf_counter() - started) * 1000
        finally:
            client.close()

    def test_normal_crypto_load_and_burst_replay_have_separate_metrics(self):
        payments = [self.payment(index) for index in range(100)]
        bodies = [self.envelope(payment_result(payment, payer={'openid': 'discard-me'}, attach='discard-me'),
                                f'normal-{index}') for index, payment in enumerate(payments)]
        self.start_process('worker')
        results, peak, duration = parallel(100, 2, lambda index: self.send(bodies[index]))
        statuses = Counter(status for status, _ in results)
        latencies = [latency for status, latency in results if status == 204]
        # No valid cohort SLO exists unless every expected receipt succeeded.
        # Fast 4xx/5xx responses must never make a failing run look faster.
        p95 = sorted(latencies)[94] if len(latencies) == 100 else None
        # Report even a failed SLO; rejected requests never improve this metric.
        report('NOTIFICATION_NORMAL', server='Waitress', threads=2, connection_limit=32,
               crypto='RSA-2048 SHA256 PKCS1v15 + AES-256-GCM', requests=100, concurrency=2,
               observed_peak_concurrency=peak, http_statuses={status: count for status, count in statuses.items() if status},
               transport_errors=dict(self.transport_errors),
               successful_responses=len(latencies), p95_ms=round(p95, 3) if p95 is not None else None,
               median_ms=round(statistics.median(latencies), 3) if latencies else None,
               duration_seconds=duration,
               failures=[] if p95 is not None and p95 < 500 else ['normal_load_slo'])
        self.assertEqual(statuses, {204: 100})
        self.assertIsNotNone(p95)
        self.assertLess(p95, 500)
        for index in range(100):
            self.await_status(f'normal-{index}')
        self.assertEqual(PaymentAttempt.objects.filter(status='paid').count(), 100)
        self.assertEqual(Order.objects.filter(payment_status='paid').count(), 100)
        self.assertEqual(AuditLog.objects.filter(action='wechat_payment_confirmed').count(), 100)
        self.assertFalse(any('payer' in row.resource or 'attach' in row.resource for row in PaymentNotification.objects.all()))
        receipts_before_replay = list(PaymentNotification.objects.order_by('id').values(
            'id', 'status', 'attempts', 'processed_at', 'lease_until', 'lease_token'))
        errors_before_replay = self.transport_errors.copy()
        replay_results, replay_peak, _ = parallel(100, 20, lambda _: self.send(bodies[0]))
        replay_statuses = Counter(status for status, _ in replay_results)
        replay_latencies = [duration for status, duration in replay_results if status == 204]
        replay_audits = AuditLog.objects.filter(action='wechat_payment_confirmed').count()
        report('NOTIFICATION_BURST', requests=100, concurrency=20, observed_peak_concurrency=replay_peak,
               http_statuses={status: count for status, count in replay_statuses.items() if status},
               transport_errors=dict(self.transport_errors - errors_before_replay),
               p95_ms=round(sorted(replay_latencies)[94], 3) if len(replay_latencies) == 100 else None,
               duplicated_changes=replay_audits - 100,
               failures=[] if replay_statuses == {204: 100} and replay_audits == 100 else ['burst_replay'])
        self.assertEqual(replay_statuses, {204: 100})
        self.assertEqual(PaymentNotification.objects.count(), 100)
        self.assertEqual(replay_audits, 100)
        self.assertEqual(list(PaymentNotification.objects.order_by('id').values(
            'id', 'status', 'attempts', 'processed_at', 'lease_until', 'lease_token')), receipts_before_replay)
        # Signed invalid requests and body overload belong to separate cohorts.
        before = PaymentNotification.objects.count()
        invalid_event = 'must-not-persist-invalid-signature'
        invalid = self.send(self.envelope(payment_result(payments[0]), invalid_event), invalid_signature=True)
        oversized = self.oversized_request()
        self.assertEqual(invalid[0], 503)
        self.assertEqual(oversized[0], 413)
        self.assertFalse(PaymentNotification.objects.filter(event_id=invalid_event).exists())
        self.assertEqual(PaymentNotification.objects.count(), before)
        report('NOTIFICATION_REJECTION', requests=2, concurrency=1,
               invalid_signature_status=invalid[0], oversized_status=oversized[0],
               invalid_signature_ms=round(invalid[1], 3), oversized_ms=round(oversized[1], 3),
               new_invalid_event_absent=True,
               included_in_normal_slo=False)
        # Exercise evidence collection with a real connection failure after the
        # normal and burst cohorts have finished; no mock affects their latency.
        self.callback.kill()
        self.callback.wait(timeout=10)
        self.assertEqual(self.send(bodies[0])[0], 0)
        self.assertEqual(sum(self.transport_errors.values()), 1)
        report('NOTIFICATION_TRANSPORT_FAILURE', requests=1, callback_stopped=True,
               transport_errors=dict(self.transport_errors), expected_fault=True,
               included_in_normal_slo=False)

    def test_connection_overload_is_bounded_and_recovers(self):
        held = []
        log = self.path / 'callback-0.log'
        try:
            # Incomplete headers consume real Waitress channels without putting
            # sleep hooks or error switches into production application code.
            for _ in range(40):
                channel = socket.create_connection(('127.0.0.1', self.port), timeout=2)
                held.append(channel)
                channel.sendall(b'POST /api/v1/payments/wechat/notify/runtime-account HTTP/1.1\r\nHost: 127.0.0.1\r\n')
            deadline = time.monotonic() + 5
            while 'reached the connection limit' not in log.read_text(errors='replace') and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertIn('reached the connection limit', log.read_text(errors='replace'))
            started = time.perf_counter()
            with self.assertRaises((TimeoutError, URLError)):
                with build_opener(ProxyHandler({})).open(
                        f'http://127.0.0.1:{self.port}/callback-health', timeout=.5) as response:
                    response.read()
            rejection_ms = (time.perf_counter() - started) * 1000
            self.assertIsNone(self.callback.poll())
            self.assertFalse(PaymentNotification.objects.exists())
        finally:
            for channel in held:
                channel.close()
        self.wait_for_callback()
        payment = self.payment()
        self.start_process('worker')
        status, latency = self.send(self.envelope(payment_result(payment), 'after-overload'))
        self.assertEqual(status, 204)
        self.await_status('after-overload')
        payment.refresh_from_db()
        payment.order.refresh_from_db()
        self.assertEqual((payment.status, payment.order.payment_status), ('paid', 'paid'))
        self.assertEqual(AuditLog.objects.filter(action='wechat_payment_confirmed').count(), 1)
        report('NOTIFICATION_OVERLOAD', attempted_connections=40, connection_limit=32,
               overflow_observed=True, extra_request='client_timeout',
               extra_request_timeout_ms=round(rejection_ms, 3), recovers_after_release=True,
               recovery_notification_status=status, recovery_notification_ms=round(latency, 3),
               recovery_payment_changes=1,
               included_in_normal_slo=False)

    def test_order_lock_durable_receipt_callback_crash_and_conflict(self):
        payment = self.payment()
        resource = payment_result(payment)
        body = self.envelope(resource, 'durable-locked')
        with transaction.atomic():
            Order.objects.select_for_update().get(pk=payment.order_id)
            status, latency = self.send(body)
            self.assertEqual(status, 204)
            self.assertLess(latency, 500)
            self.assertEqual(PaymentNotification.objects.get(event_id='durable-locked').status, 'pending')
            self.callback.kill()
            self.callback.wait(timeout=10)
        self.callback = self.start_process('callback')
        self.wait_for_callback()
        self.assertEqual(self.send(body)[0], 204)
        conflict = self.envelope({**resource, 'amount': {'total': payment.amount_cents + 1, 'currency': 'CNY'}}, 'durable-locked')
        self.assertEqual(self.send(conflict)[0], 400)
        self.start_process('worker')
        row = self.await_status('durable-locked')
        self.assertEqual(row.resource, resource)
        self.assertEqual(row.conflict_count, 1)
        self.assertEqual(AuditLog.objects.filter(action='wechat_payment_confirmed').count(), 1)
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'paid')
        report('NOTIFICATION_DURABILITY', requests=3, concurrency=1, order_lock_ack_ms=round(latency, 3),
               receipt_survives_callback_kill=True, conflict_preserves_original=True,
               callback_creation_disabled=True, payment_changes=1)

    def test_claim_process_kill_recovers_after_real_lease_expiry(self):
        payment = self.payment()
        self.assertEqual(self.send(self.envelope(payment_result(payment), 'lease-crash'))[0], 204)
        claimer = self.start_process('claim-and-pause')
        marker = self.path / 'claimed.json'
        deadline = time.monotonic() + 20
        while not marker.exists() and time.monotonic() < deadline:
            self.assertIsNone(claimer.poll(), 'Claim process exited unexpectedly')
            time.sleep(0.05)
        self.assertTrue(marker.exists())
        claimed = PaymentNotification.objects.get(event_id='lease-crash')
        self.assertEqual((claimed.status, claimed.attempts), ('processing', 1))
        lease_until = claimed.lease_until
        claimer.kill()
        claimer.wait(timeout=10)
        started = time.monotonic()
        self.start_process('worker')
        time.sleep(.25)
        self.assertEqual(PaymentNotification.objects.get(pk=claimed.pk).attempts, 1)
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'pending')
        row = self.await_status('lease-crash', timeout=75)
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(timezone.now(), lease_until)
        self.assertEqual(row.attempts, 2)
        self.assertIsNone(row.lease_until)
        self.assertIsNone(row.lease_token)
        self.assertIsNotNone(row.processed_at)
        payment.refresh_from_db()
        payment.order.refresh_from_db()
        self.assertEqual((payment.status, payment.order.payment_status), ('paid', 'paid'))
        self.assertEqual(payment.transaction_id, 'WX' + payment.out_trade_no)
        self.assertIsNotNone(payment.paid_at)
        self.assertIsNotNone(payment.order.paid_at)
        self.assertEqual(AuditLog.objects.filter(action='wechat_payment_confirmed').count(), 1)
        self.assertGreater(elapsed, 55)
        report('NOTIFICATION_LEASE_RECOVERY', process_killed=True, lease_seconds=60,
               actual_wait_seconds=round(elapsed, 3), clock_modified=False,
               attempts=2, payment_changes=1, payment_status=payment.status,
               order_payment_status=payment.order.payment_status, lease_cleared=True)

    def test_refund_out_of_order_does_not_reverse_success(self):
        payment = self.payment()
        self.start_process('worker')
        self.assertEqual(self.send(self.envelope(payment_result(payment), 'refund-payment'))[0], 204)
        self.await_status('refund-payment')
        payment.refresh_from_db()
        refund = PaymentRefund.objects.create(payment=payment, order=payment.order,
            out_refund_no='RUNTIME-REFUND', amount_cents=payment.amount_cents, reason='隔离退款验收', status='processing')
        Order.objects.filter(pk=payment.order_id).update(payment_status='refunding')
        good = self.envelope(refund_result(refund, notification=True), 'refund-success', 'REFUND.SUCCESS')
        self.assertEqual(self.send(good)[0], 204)
        self.await_status('refund-success')
        stale = self.envelope(refund_result(refund, 'ABNORMAL', notification=True), 'refund-stale', 'REFUND.ABNORMAL')
        self.assertEqual(self.send(stale)[0], 204)
        self.await_status('refund-stale', status='dead')
        self.assertEqual(self.send(good)[0], 204)
        refund.refresh_from_db()
        payment.order.refresh_from_db()
        self.assertEqual((refund.status, payment.order.payment_status), ('success', 'refunded'))
        self.assertEqual(AuditLog.objects.filter(action='wechat_refund_succeeded').count(), 1)
        report('NOTIFICATION_REFUND_ORDER', requests=4, concurrency=1,
               success_preserved=True, stale_abnormal_quarantined=True, refund_changes=1)
