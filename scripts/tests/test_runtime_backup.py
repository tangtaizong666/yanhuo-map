"""Ownership and fault-harness checks; no real Docker or production data access."""
import importlib.util
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import subprocess
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('runtime_backup', Path(__file__).resolve().parents[1] / 'verify_runtime_backup.py')
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)


class RuntimeBackupSafetyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.marker = {'project_name': 'yanhuo-verify-fixture', 'synthetic_only': True, 'run_id': 'fixture-run'}

    def write_marker(self, **changes):
        (self.root / verify.MARKER).write_text(json.dumps({**self.marker, **changes}), encoding='utf-8')

    def test_missing_marker_and_production_project_are_rejected(self):
        with self.assertRaisesRegex(verify.VerificationError, 'marker_required'):
            verify.isolation_marker(self.root, self.marker['project_name'])
        for changes in [{'synthetic_only': False}, {'synthetic_only': 'true'}, {'run_id': ''},
                        {'project_name': 'yanhuo-production'}]:
            self.write_marker(**changes)
            with self.subTest(changes=changes), self.assertRaisesRegex(verify.VerificationError, 'marker_mismatch'):
                verify.isolation_marker(self.root, changes.get('project_name', self.marker['project_name']))

    def test_marker_must_match_exact_compose_project(self):
        self.write_marker()
        self.assertEqual(verify.isolation_marker(self.root, self.marker['project_name']), self.marker)
        with self.assertRaisesRegex(verify.VerificationError, 'marker_mismatch'):
            verify.isolation_marker(self.root, 'yanhuo-verify-other')

    def test_each_writer_and_database_requires_same_run_label(self):
        services = {service: 'owned-' + service for service in ('db', *verify.backup.WRITERS)}
        with patch.object(verify.backup, 'containers', return_value=services), \
             patch.object(verify, 'command', return_value=json.dumps({'yanhuo.verification.run': 'foreign-run'})):
            with self.assertRaisesRegex(verify.VerificationError, 'run_label_mismatch'):
                verify.assert_stack_isolation(object(), self.marker)
        with patch.object(verify.backup, 'containers', return_value={'backend': 'owned'}):
            with self.assertRaisesRegex(verify.VerificationError, 'all_disposable_writers'):
                verify.assert_stack_isolation(object(), self.marker)

    def test_cleanup_never_removes_an_unowned_resource(self):
        for kind in ('container', 'volume'):
            with self.subTest(kind=kind), patch.object(verify, 'command', return_value='{}') as command:
                with self.assertRaisesRegex(verify.VerificationError, 'ownership_mismatch'):
                    verify.remove_owned(kind, 'foreign-id', 'fixture-run')
                self.assertEqual(command.call_count, 1)

    def test_cleanup_uses_verified_id_and_label(self):
        with patch.object(verify, 'command', side_effect=[json.dumps({verify.LABEL: 'fixture-run'}), '']) as command:
            verify.remove_owned('container', 'owned-id', 'fixture-run')
        self.assertEqual(command.call_args.args[0], ['docker', 'rm', '--force', 'owned-id'])

    def test_resumed_services_must_be_exact_original_ids(self):
        original = {'db': 'db-id', **{service: 'owned-' + service for service in verify.backup.WRITERS}}
        config = SimpleNamespace(maintenance=self.root / 'maintenance.json')
        with patch.object(verify.backup, 'containers', return_value={key: value for key, value in original.items() if key != 'db'}):
            verify.assert_writers_resumed(config, original)
        with patch.object(verify.backup, 'containers', return_value={'backend': 'replacement-id'}):
            with self.assertRaisesRegex(verify.VerificationError, 'original_writers_not_resumed'):
                verify.assert_writers_resumed(config, original)

    def test_failed_cli_evidence_is_retained_before_assertion(self):
        config = SimpleNamespace(env={})
        failure = SimpleNamespace(returncode=1, stdout=b'', stderr=b'{"reason":"fixture"}\n')
        with patch.object(verify.subprocess, 'run', return_value=failure):
            with self.assertRaisesRegex(verify.VerificationError, 'unexpected_backup_result'):
                verify.run_backup_cli(config, 'backup', self.root, 'failed-capture')
        self.assertEqual((self.root / 'failed-capture.log').read_bytes(), failure.stderr)

    def test_corruption_child_uses_real_entry_and_explicit_good_snapshot(self):
        config = SimpleNamespace(env={})
        failure = SimpleNamespace(returncode=1, stdout=b'test_fault=corrupted_downloaded_dump\n', stderr=b'')
        with patch.object(verify.subprocess, 'run', return_value=failure) as run:
            verify.run_backup_cli(config, 'restore-check', self.root, 'corrupt-restore',
                                  snapshot='good-full-snapshot', corrupt_restore=True, expect_success=False)
        args = run.call_args.args[0]
        self.assertEqual(args[1], '-c')
        compile(args[2], '<isolated-restore-fault>', 'exec')
        self.assertIn('sys.exit(module.main())', args[2])
        self.assertEqual(args[-3:], ['restore-check', '--snapshot', 'good-full-snapshot'])
        self.assertNotIn('damaged dump fixture', Path(verify.backup.__file__).read_text())
        with patch.object(verify.subprocess, 'run') as run:
            with self.assertRaisesRegex(verify.VerificationError, 'requires_restore_check'):
                verify.run_backup_cli(config, 'backup', self.root, 'invalid', corrupt_restore=True)
            run.assert_not_called()

    def test_restore_monitor_evidence_survives_failed_assertion(self):
        state = self.root / 'state'
        state.mkdir()
        now = datetime.now(timezone.utc).isoformat()
        verify.backup.write_json(state / 'last-success.json', {'completed_at': now, 'captured_at': now})
        verify.backup.write_json(state / 'last-restore-failure.json',
                                 {'operation': 'restore-check', 'failed_at': now})
        config = SimpleNamespace(state=state, rpo=3600)
        with self.assertRaisesRegex(verify.VerificationError, 'incorrect_restore_monitor_result'):
            verify.record_restore_monitor(config, self.root, 'unexpected-restore-incident', restore_failed=False)
        evidence = json.loads((self.root / 'unexpected-restore-incident.json').read_text())
        self.assertEqual(evidence['issues'], {'restore_failed': 1})
        self.assertEqual(evidence['last-restore-failure.json']['operation'], 'restore-check')
        self.assertIsNone(evidence['last-restore.json'])

    def test_retained_good_snapshot_must_preserve_data_tree_and_capture_time(self):
        config = SimpleNamespace(name='yanhuo-verify-fixture', tag='yanhuo-fixture')
        before = {'id': 'original-id', 'tree': 'good-tree', 'time': '2026-10-03T00:00:00Z'}
        after = {**before, 'id': 'retagged-id'}
        with patch.object(verify.backup, 'restic', side_effect=[json.dumps([before]).encode(), b'', json.dumps([after]).encode()]):
            retained = verify.retain_fixture_snapshot(config, 'original-id', 'unique-run')
        self.assertEqual(retained['retained_snapshot_id'], 'retagged-id')
        self.assertEqual(retained['tree'], 'good-tree')
        for change in ({'tree': 'another-tree'}, {'time': '2026-10-03T01:00:00Z'}):
            with self.subTest(change=change), patch.object(verify.backup, 'restic', side_effect=[
                    json.dumps([before]).encode(), b'', json.dumps([{**after, **change}]).encode()]):
                with self.assertRaisesRegex(verify.VerificationError, 'snapshot_content_changed'):
                    verify.retain_fixture_snapshot(config, 'original-id', 'unique-run')

    def test_wrapper_fault_is_bound_to_exact_database_and_pg_dump(self):
        with patch.object(verify.shutil, 'which', side_effect=lambda name: '/usr/bin/' + name):
            wrappers = verify.tool_wrappers(self.root, ['ssh', '-s', 'sftp'], 'owned-db-id')
        source = (wrappers / 'docker').read_text()
        self.assertIn("['exec', 'owned-db-id', 'pg_dump']", source)
        self.assertIn('YANHUO_TEST_FAIL_DUMP', source)
        production_source = Path(verify.backup.__file__).read_text()
        self.assertNotIn('YANHUO_TEST_FAIL_DUMP', production_source)

    def test_running_containers_without_post_restart_progress_are_not_recovered(self):
        started = datetime(2026, 10, 3, tzinfo=timezone.utc)
        states = {name: {'running': True, 'health': 'healthy', 'started_at': started.isoformat()}
                  for name in verify.backup.WRITERS}
        probe = {'api': {'status': 'ok', 'http_status': 200},
                 'heartbeats': {name: {'last_success_epoch': started.timestamp() + 1}
                                for name in verify.WORKER_NAMES.values()}}
        self.assertTrue(verify.recovery_is_complete(states, probe))
        for name in verify.WORKER_NAMES.values():
            with self.subTest(worker=name):
                stale = json.loads(json.dumps(probe))
                stale['heartbeats'][name]['last_success_epoch'] = started.timestamp() - 1
                self.assertFalse(verify.recovery_is_complete(states, stale))

    def test_recovery_requires_every_original_healthcheck_and_http_success(self):
        states = {name: {'running': True, 'health': 'healthy', 'started_at': '2026-10-03T00:00:00Z'}
                  for name in verify.backup.WRITERS}
        probe = {'api': {'status': 'ok', 'http_status': 200},
                 'heartbeats': {name: {'last_success_epoch': 2000000000}
                                for name in verify.WORKER_NAMES.values()}}
        for service in verify.backup.WRITERS:
            unready = json.loads(json.dumps(states))
            unready[service]['health'] = 'starting'
            with self.subTest(service=service):
                self.assertFalse(verify.recovery_is_complete(unready, probe))
        probe['api']['http_status'] = 503
        self.assertFalse(verify.recovery_is_complete(states, probe))

    def test_recovery_timeout_keeps_partial_probe_and_container_state(self):
        original = {name: 'owned-' + name for name in verify.backup.WRITERS}
        config = SimpleNamespace(name='yanhuo-verify-fixture', project=self.root)
        rows = [{'Id': identifier, 'Config': {'Labels': {
                    'com.docker.compose.project': config.name,
                    'com.docker.compose.project.working_dir': str(self.root),
                    'com.docker.compose.service': name}},
                 'State': {'Running': True, 'Health': {'Status': 'healthy'},
                           'StartedAt': '2026-10-03T00:00:00Z'}} for name, identifier in original.items()]
        timeout = subprocess.TimeoutExpired('fixture', 30, output=b'partial probe', stderr=b'test timeout')
        with patch.object(verify, 'assert_writers_resumed'), \
             patch.object(verify, 'command', return_value=json.dumps(rows)), \
             patch.object(verify.subprocess, 'run', side_effect=timeout), \
             patch.object(verify.time, 'monotonic', side_effect=[0, 0, 151]):
            with self.assertRaisesRegex(verify.VerificationError, 'did_not_recover'):
                verify.assert_writers_recovered(config, original, self.root)
        self.assertIn('partial probe', (self.root / 'writer-recovery-0.log').read_text())
        attempts = json.loads((self.root / 'writer-recovery-attempts.json').read_text())
        self.assertEqual(attempts[0]['probe_error'], 'timed_out')
        self.assertEqual(set(attempts[0]['containers']), set(verify.backup.WRITERS))

    def test_sftp_cleanup_is_recorded_even_when_verification_body_fails(self):
        cleanup = {'auxiliary_resources_removed': False}
        def command(args, **kwargs):
            if args[0] == 'ssh-keygen':
                Path(str(args[-1]) + '.pub').write_text('ssh-ed25519 AAAA synthetic-key')
            if args[:2] == ['docker', 'create']:
                return 'owned-sftp-id'
            if args[:2] == ['docker', 'inspect']:
                return json.dumps([{'NetworkSettings': {'Ports': {
                    '22/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '2222'}]}}}])
            return ''
        with patch.object(verify, 'command', side_effect=command), \
             patch.object(verify.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=b'', stderr=b'')), \
             patch.object(verify, 'remove_owned') as remove:
            with self.assertRaisesRegex(RuntimeError, 'fixture_body_failure'):
                with verify.sftp_fixture(self.root, 'fixture-run', self.root, cleanup):
                    raise RuntimeError('fixture_body_failure')
        self.assertTrue(cleanup['auxiliary_resources_removed'])
        self.assertEqual([call.args for call in remove.call_args_list], [
            ('container', 'owned-sftp-id', 'fixture-run'),
            ('volume', 'yanhuo-backup-sftp-fixture-run', 'fixture-run')])

    @unittest.skipIf(os.name == 'nt', 'POSIX private file permissions')
    def test_test_credentials_are_owner_only(self):
        path = verify.private_write(self.root / 'private', 'synthetic-secret')
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
