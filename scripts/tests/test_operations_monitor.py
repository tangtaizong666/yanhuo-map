"""Isolated host-monitor tests; only a loopback HTTPS receiver is contacted."""
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

MODULE = Path(__file__).resolve().parents[1] / 'operations_monitor.py'
SPEC = importlib.util.spec_from_file_location('yanhuo_monitor', MODULE)
monitor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(monitor)


def healthy_operations():
    return {'status': 'ok', 'workers': [{'name': name, 'healthy': True} for name in monitor.WORKERS],
            'notifications': {'dead': 0, 'conflicts': 0, 'pending': 0, 'overdue': False},
            'payments': {'pending': 0, 'refunds_pending': 0, 'reviews_required': 0, 'overdue': False},
            'order_expiry': {'eligible_pending': 0, 'overdue_count': 0, 'oldest_overdue_seconds': None, 'overdue': False}}


class MonitorFixture(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.project = self.root / 'project'
        self.project.mkdir()
        (self.project / 'compose.yaml').write_text('services: {}', encoding='utf-8')
        self.private = self.root / 'private'
        self.private.mkdir(mode=0o700)
        self.backup = self.private / 'backup'
        self.backup.mkdir(mode=0o700)
        self.env = self.write_private('production.env', 'DO_NOT_LEAK=customer-payment-secret')
        self.webhook = self.write_private('webhook', 'https://alerts.invalid/private-token')
        self.values = {'project_dir': str(self.project), 'project_name': 'yanhuo-monitor-fixture',
                       'env_file': str(self.env), 'webhook_file': str(self.webhook),
                       'state_dir': str(self.private / 'state'), 'backup_state_dir': str(self.backup)}
        self.filename = self.write_private('operations.json', json.dumps(self.values))
        self.config = monitor.Config(self.filename)
        self.now = datetime.now(timezone.utc).timestamp()

    def write_private(self, name, value):
        path = self.private / name
        path.write_text(value, encoding='utf-8')
        os.chmod(path, 0o600)
        return path

    def check(self, issues, now=None):
        return monitor.check(monitor.Config(self.filename), now=self.now if now is None else now,
                             collector=lambda config, clock: issues)


class MonitorTests(MonitorFixture):
    def test_restart_deduplicates_reminder_and_single_recovery(self):
        with patch.object(monitor, 'deliver') as send:
            self.check({'notifications_dead': 2})
            first = send.call_args.args[1]
            self.assertEqual(first['kind'], 'problem')
            self.check({'notifications_dead': 3}, self.now + 60)
            self.assertEqual(send.call_count, 1)
            self.check({'notifications_dead': 3}, self.now + 1800)
            self.assertEqual(send.call_args.args[1]['kind'], 'reminder')
            self.check({}, self.now + 1860)
            self.assertEqual(send.call_args.args[1]['kind'], 'recovery')
            self.check({}, self.now + 1920)
            self.assertEqual(send.call_count, 3)
        self.assertEqual(set(first), {'event_id', 'kind', 'category', 'count', 'observed_at', 'first_seen_at', 'runbook'})

    def test_failed_delivery_persists_id_and_retry_schedule_across_restart(self):
        with patch.object(monitor, 'deliver', side_effect=monitor.MonitorError('webhook_delivery_failed')) as send:
            report = self.check({'notifications_conflicts': 1})
            event_id = send.call_args.args[1]['event_id']
            self.assertEqual(report['delivery'], {'delivered': 0, 'failed': 1, 'pending': 1})
            self.check({'notifications_conflicts': 1}, self.now + 30)
            self.assertEqual(send.call_count, 1)
        with patch.object(monitor, 'deliver') as send:
            report = self.check({'notifications_conflicts': 1}, self.now + 60)
            self.assertEqual(send.call_args.args[1]['event_id'], event_id)
            self.assertEqual(report['delivery']['pending'], 0)
            self.check({'notifications_conflicts': 1}, self.now + 1800)
            self.assertEqual(send.call_count, 1, '30-minute reminders start after successful delivery')

    def test_recovery_queues_behind_failed_problem_and_endpoint_is_not_flooded(self):
        with patch.object(monitor, 'deliver', side_effect=monitor.MonitorError('webhook_delivery_failed')) as send:
            self.check({'notifications_dead': 1, 'payments_review': 2})
            self.assertEqual(send.call_count, 1)
        with patch.object(monitor, 'deliver') as send:
            self.check({}, self.now + 60)
            self.assertEqual([call.args[1]['kind'] for call in send.call_args_list], ['problem', 'problem', 'recovery', 'recovery'])

    def test_live_workers_do_not_hide_business_failure(self):
        record = healthy_operations()
        record.update(status='attention')
        record['notifications'].update(dead=2, conflicts=3, pending=4, overdue=True)
        record['payments'].update(pending=5, refunds_pending=2, reviews_required=1, overdue=True)
        record['order_expiry'].update(eligible_pending=4, overdue_count=3, oldest_overdue_seconds=180, overdue=True)
        with patch.object(monitor, 'run', return_value=SimpleNamespace(returncode=1, stdout=json.dumps(record).encode())) as command:
            issues = monitor.business_issues(self.config)
        self.assertEqual(issues, {'notifications_dead': 2, 'notifications_conflicts': 3, 'notifications_backlog': 4,
                                  'payments_backlog': 7, 'payments_review': 1, 'orders_expiry_backlog': 3})
        self.assertEqual(command.call_args.args[0][-6:], ['--max-heartbeat-age', '120', '--max-payment-age', '600',
                                                       '--max-expiry-age', '120'])

    def test_unavailable_sources_cannot_announce_unverified_recovery(self):
        original = {'notifications_dead': 2, 'payments_review': 1, 'containers_unhealthy': 1,
                    'backup_stale': 1, 'orders_expiry_backlog': 2, 'restore_failed': 1}
        unavailable = {'operations_unavailable': 1, 'containers_unavailable': 1, 'backup_unavailable': 1}
        with patch.object(monitor, 'deliver') as send:
            self.check(original)
            send.reset_mock()
            self.check(unavailable, self.now + 60)
            self.assertEqual({call.args[1]['category'] for call in send.call_args_list}, set(unavailable))
            self.assertTrue(all(call.args[1]['kind'] == 'problem' for call in send.call_args_list))
            state = monitor.load_state(self.config)
            self.assertTrue(all(state['incidents'][category]['active'] for category in original))
            send.reset_mock()
            self.check(unavailable, self.now + 1860)
            self.assertTrue(all(call.args[1]['category'] in unavailable for call in send.call_args_list),
                            'unknown old incidents must not generate reminders either')
            send.reset_mock()
            self.check({}, self.now + 1920)
            self.assertEqual({call.args[1]['category'] for call in send.call_args_list}, set(original) | set(unavailable))
            self.assertTrue(all(call.args[1]['kind'] == 'recovery' for call in send.call_args_list))

    def test_unverified_container_ownership_prevents_backend_exec(self):
        with patch.object(monitor, 'container_issues', return_value={'containers_unavailable': 1}), \
             patch.object(monitor, 'business_issues') as business, \
             patch.object(monitor, 'backup_issues', return_value={}):
            self.assertEqual(monitor.collect(self.config, self.now),
                             {'containers_unavailable': 1, 'operations_unavailable': 1})
        business.assert_not_called()

    def test_failed_or_incomplete_check_cannot_look_healthy(self):
        for output in [b'password=secret', json.dumps({'status': 'ok'}).encode(), json.dumps({**healthy_operations(), 'workers': []}).encode()]:
            with self.subTest(output=output), patch.object(monitor, 'run', return_value=SimpleNamespace(returncode=1, stdout=output)):
                self.assertEqual(monitor.business_issues(self.config), {'operations_unavailable': 1})

    def test_backup_freshness_uses_capture_and_detects_maintenance(self):
        self.assertEqual(monitor.backup_issues(self.config, self.now), {'backup_unavailable': 1})
        monitor.write_json(self.backup / 'last-success.json', {'captured_at': monitor.timestamp(self.now - 3601), 'completed_at': monitor.timestamp(self.now)})
        (self.backup / 'maintenance.json').write_text('{}')
        self.assertEqual(monitor.backup_issues(self.config, self.now), {'backup_stale': 1, 'backup_maintenance': 1})
        monitor.write_json(self.backup / 'last-success.json', {'captured_at': monitor.timestamp(self.now + 120), 'completed_at': monitor.timestamp(self.now)})
        self.assertIn('backup_unavailable', monitor.backup_issues(self.config, self.now))

    def test_expiry_diagnostics_must_be_complete_and_consistent(self):
        missing = healthy_operations()
        missing.pop('order_expiry')
        records = [missing]
        for change in ({'overdue_count': 1}, {'eligible_pending': 1},
                       {'eligible_pending': 1, 'oldest_overdue_seconds': -1},
                       {'eligible_pending': 1, 'oldest_overdue_seconds': float('nan')},
                       {'overdue': 'false'}, {'oldest_overdue_seconds': 10}):
            record = healthy_operations()
            record['order_expiry'].update(change)
            records.append(record)
        for record in records:
            with self.subTest(record=record), patch.object(monitor, 'run',
                    return_value=SimpleNamespace(returncode=0, stdout=json.dumps(record).encode())):
                self.assertEqual(monitor.business_issues(self.config), {'operations_unavailable': 1})

    def test_restore_failure_needs_actual_restore_success_and_recovery_is_sent_once(self):
        success = self.backup / 'last-success.json'
        failure = {'operation': 'restore-check', 'failed_at': monitor.timestamp(self.now - 30)}
        monitor.write_json(success, {'captured_at': monitor.timestamp(self.now - 120),
                                    'completed_at': monitor.timestamp(self.now - 90)})
        # Compatibility with failures from before the dedicated record existed.
        monitor.write_json(self.backup / 'last-failure.json', failure)
        with patch.object(monitor, 'deliver') as send:
            issues = monitor.backup_issues(self.config, self.now)
            self.assertEqual(issues, {'restore_failed': 1})
            self.check(issues)
            self.assertEqual(send.call_args.args[1]['kind'], 'problem')
            monitor.write_json(success, {'captured_at': monitor.timestamp(self.now + 30),
                                        'completed_at': monitor.timestamp(self.now + 45)})
            self.check(monitor.backup_issues(self.config, self.now + 60), self.now + 60)
            self.assertEqual(send.call_count, 1, 'a new capture must not announce restore recovery')
            monitor.write_json(self.backup / 'last-restore-failure.json', failure)
            # A later backup failure cannot hide the independent restore issue.
            monitor.write_json(self.backup / 'last-failure.json',
                               {'operation': 'backup', 'failed_at': monitor.timestamp(self.now + 80)})
            self.assertEqual(monitor.backup_issues(self.config, self.now + 90),
                             {'backup_failed': 1, 'restore_failed': 1})
            monitor.write_json(success, {'captured_at': monitor.timestamp(self.now + 100),
                                        'completed_at': monitor.timestamp(self.now + 110)})
            monitor.write_json(self.backup / 'last-restore.json',
                               {'completed_at': monitor.timestamp(self.now + 120), 'snapshot_id': 'verified',
                                'tables_verified': 40, 'files_verified': 8, 'gateway_network': 'disabled'})
            self.check(monitor.backup_issues(self.config, self.now + 130), self.now + 130)
            self.assertEqual(send.call_count, 2)
            self.assertEqual(send.call_args.args[1]['kind'], 'recovery')
            self.check(monitor.backup_issues(self.config, self.now + 150), self.now + 150)
            self.assertEqual(send.call_count, 2)

    def test_incomplete_old_future_or_corrupt_restore_evidence_cannot_clear_failure(self):
        monitor.write_json(self.backup / 'last-success.json',
                           {'captured_at': monitor.timestamp(self.now), 'completed_at': monitor.timestamp(self.now)})
        monitor.write_json(self.backup / 'last-restore-failure.json',
                           {'operation': 'restore-check', 'failed_at': monitor.timestamp(self.now - 30)})
        valid = {'completed_at': monitor.timestamp(self.now), 'snapshot_id': 'verified',
                 'tables_verified': 40, 'files_verified': 8, 'gateway_network': 'disabled'}
        for change in ({'completed_at': monitor.timestamp(self.now - 60)},
                       {'completed_at': monitor.timestamp(self.now + 120)}, {'tables_verified': 0},
                       {'files_verified': 0}, {'snapshot_id': ''}, {'gateway_network': 'enabled'}):
            monitor.write_json(self.backup / 'last-restore.json', {**valid, **change})
            with self.subTest(change=change):
                self.assertEqual(monitor.backup_issues(self.config, self.now), {'restore_failed': 1})
        (self.backup / 'last-restore.json').write_text('{broken')
        self.assertEqual(monitor.backup_issues(self.config, self.now), {'restore_failed': 1})
        monitor.write_json(self.backup / 'last-restore.json', valid)
        (self.backup / 'last-restore-failure.json').write_text('{broken')
        self.assertEqual(monitor.backup_issues(self.config, self.now), {'restore_failed': 1})

    def test_container_check_verifies_labels_and_all_services(self):
        records = []
        for service in (*monitor.SERVICES, 'release'):
            records.append({'labels': {'com.docker.compose.project': self.config.name,
                                       'com.docker.compose.project.working_dir': str(self.project),
                                       'com.docker.compose.service': service},
                            'state': {'Running': service != 'release', 'Status': 'exited' if service == 'release' else 'running', 'ExitCode': 0}})
        def command(args):
            output = b'a' * 64 if 'ps' in args else '\n'.join(json.dumps(row) for row in records).encode()
            return SimpleNamespace(returncode=0, stdout=output)
        with patch.object(monitor, 'run', side_effect=command):
            self.assertEqual(monitor.container_issues(self.config), {})
            records[0]['state']['Running'] = False
            self.assertEqual(monitor.container_issues(self.config), {'containers_unhealthy': 1})
            records[0]['labels']['com.docker.compose.project'] = 'another-project'
            self.assertEqual(monitor.container_issues(self.config), {'containers_unavailable': 1})

    def test_corrupt_state_is_preserved_without_fresh_alerts(self):
        self.config.store.write_text('{broken', encoding='utf-8')
        with patch.object(monitor, 'deliver') as send, self.assertRaisesRegex(monitor.MonitorError, 'invalid_preserved'):
            self.check({'notifications_dead': 1})
        self.assertEqual(self.config.store.read_text(), '{broken')
        send.assert_not_called()

    def test_sensitive_or_unknown_payload_fields_are_never_forwarded(self):
        state = monitor.initial_state()
        monitor.reconcile(self.config, state, {'notifications_dead': 1}, self.now)
        state['outbox'][0]['payload']['customer'] = 'private-customer'
        monitor.write_json(self.config.store, state)
        with patch.object(monitor, 'deliver') as send, self.assertRaisesRegex(monitor.MonitorError, 'invalid_preserved'):
            self.check({'notifications_dead': 1})
        send.assert_not_called()

    def test_configuration_must_be_private_external_and_https(self):
        self.webhook.write_text('http://alerts.invalid')
        with self.assertRaisesRegex(monitor.MonitorError, 'https'):
            monitor.Config(self.filename)
        self.webhook.write_text('https://alerts.invalid')
        internal = self.project / 'secret.json'
        internal.write_text(json.dumps(self.values))
        os.chmod(internal, 0o600)
        with self.assertRaisesRegex(monitor.MonitorError, 'outside_checkout'):
            monitor.Config(internal)

    def test_atomic_single_runner_lock(self):
        with monitor.exclusive(self.config):
            with self.assertRaisesRegex(monitor.MonitorError, 'already_running'):
                with monitor.exclusive(self.config):
                    self.fail('second runner acquired the lock')


class HTTPSMonitorTests(MonitorFixture):
    """Real TLS and fresh interpreter per poll; logical retry clocks stay in tests."""
    def setUp(self):
        super().setUp()
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Yanhuo isolated monitor test')])
        now = datetime.now(timezone.utc)
        certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
                       .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
                       .not_valid_after(now + timedelta(hours=1))
                       .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                       .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'), x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
                       .sign(key, hashes.SHA256()))
        self.certificate = self.write_private('ca.pem', certificate.public_bytes(serialization.Encoding.PEM).decode())
        self.key = self.write_private('key.pem', key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode())
        self.requests = []
        self.responses = []
        parent = self
        class Receiver(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                parent.requests.append({'payload': json.loads(self.rfile.read(int(self.headers['Content-Length']))),
                                        'idempotency_key': self.headers.get('Idempotency-Key')})
                status = parent.responses.pop(0) if parent.responses else 204
                self.send_response(status)
                if status == 302:
                    self.send_header('Location', f'https://127.0.0.1:{parent.server.server_port}/redirected')
                self.end_headers()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Receiver)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.certificate, self.key)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.webhook.write_text(f'https://127.0.0.1:{self.server.server_port}/alerts')
        self.values['ca_file'] = str(self.certificate)
        self.filename.write_text(json.dumps(self.values))

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def subprocess_check(self, issues, now):
        # The dependency injection exists only in this test program. The public
        # monitor CLI has no fake statuses, fault switches, or adjustable clock.
        program = ('import importlib.util,json,sys; '
                   's=importlib.util.spec_from_file_location("monitor",sys.argv[1]); '
                   'm=importlib.util.module_from_spec(s); s.loader.exec_module(m); '
                   'r=m.check(m.Config(sys.argv[2]),now=float(sys.argv[3]),collector=lambda c,n:json.loads(sys.argv[4])); '
                   'print(json.dumps(r))')
        result = subprocess.run([sys.executable, '-c', program, str(MODULE), str(self.filename), str(now), json.dumps(issues)],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_real_https_failure_restart_retry_and_recovery(self):
        self.responses[:] = [503, 204, 204]
        first = self.subprocess_check({'notifications_dead': 2}, self.now)
        self.assertEqual(first['delivery']['failed'], 1)
        stable_id = self.requests[0]['payload']['event_id']
        state = monitor.read_json(self.config.store)
        self.assertEqual(state['outbox'][0]['attempts'], 1)
        self.subprocess_check({'notifications_dead': 2}, self.now + 30)
        self.assertEqual(len(self.requests), 1)
        recovered_delivery = self.subprocess_check({'notifications_dead': 2}, self.now + 61)
        self.assertEqual(recovered_delivery['delivery']['delivered'], 1)
        self.assertEqual(self.requests[1]['payload']['event_id'], stable_id)
        self.assertEqual(self.requests[1]['idempotency_key'], stable_id)
        final = self.subprocess_check({}, self.now + 121)
        self.assertEqual(final['status'], 'ok')
        self.assertEqual(self.requests[2]['payload']['kind'], 'recovery')
        self.subprocess_check({}, self.now + 181)
        self.assertEqual(len(self.requests), 3)
        serialized = json.dumps(self.requests)
        self.assertNotIn('private-token', serialized)
        self.assertNotIn('customer-payment-secret', serialized)

    def test_tls_verification_is_required_and_redirects_are_not_followed(self):
        self.values.pop('ca_file')
        self.filename.write_text(json.dumps(self.values))
        report = self.subprocess_check({'notifications_dead': 1}, self.now)
        self.assertEqual(report['delivery']['failed'], 1)
        self.assertEqual(self.requests, [])
        self.values['ca_file'] = str(self.certificate)
        self.filename.write_text(json.dumps(self.values))
        self.responses[:] = [302]
        report = self.subprocess_check({'notifications_dead': 1}, self.now + 61)
        self.assertEqual(report['delivery']['failed'], 1)
        self.assertEqual(len(self.requests), 1)


if __name__ == '__main__':
    unittest.main()
