"""No Docker, production file, repository, database, or webhook is contacted."""
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import MagicMock, patch

SPEC = importlib.util.spec_from_file_location('yanhuo_backup', Path(__file__).resolve().parents[1] / 'backup.py')
backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup)


class BackupSafetyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.project = self.root / 'project'
        self.project.mkdir()
        (self.project / 'compose.yaml').write_text('services: {}', encoding='utf-8')
        self.private = self.root / 'private'
        self.private.mkdir(mode=0o700)
        for name, value in [('production.env', 'DJANGO_ENV=production'), ('password', 'fixture-password'),
                            ('webhook', 'https://alerts.invalid/fixture')]:
            path = self.private / name
            path.write_text(value, encoding='utf-8')
            os.chmod(path, 0o600)
        self.env = {
            'BACKUP_PROJECT_DIR': str(self.project), 'BACKUP_PROJECT_NAME': 'yanhuo-fixture',
            'BACKUP_ENV_FILE': str(self.private / 'production.env'), 'BACKUP_STATE_DIR': str(self.private / 'state'),
            'RESTIC_REPOSITORY': 'rest:https://backup.invalid/fixture', 'RESTIC_PASSWORD_FILE': str(self.private / 'password'),
            'BACKUP_FAILURE_WEBHOOK_FILE': str(self.private / 'webhook'), 'BACKUP_PAYMENTS_CONFIGURED': 'false',
            'BACKUP_OFFSITE_CONFIRMED': 'true', 'BACKUP_MAINTENANCE_APPROVED': 'true',
        }
        self.config = backup.Config(self.env)

    def test_unconfigured_or_local_repository_is_rejected_without_running_tools(self):
        with patch.object(backup, 'run') as command:
            for repository in ['', str(self.root), 'local:/backup', 'rest:http://backup.invalid']:
                with self.subTest(repository=repository), self.assertRaises(backup.BackupError):
                    backup.Config({**self.env, 'RESTIC_REPOSITORY': repository})
            command.assert_not_called()

    def test_payment_secrets_are_required_even_when_new_payments_are_disabled(self):
        with self.assertRaises(backup.BackupError):
            backup.Config({**self.env, 'BACKUP_PAYMENTS_CONFIGURED': 'true'})

    def test_wrong_compose_project_or_directory_cannot_be_stopped(self):
        labels = {'com.docker.compose.project': self.config.name,
                  'com.docker.compose.project.working_dir': str(self.project),
                  'com.docker.compose.service': 'backend'}
        for change in ({'com.docker.compose.project': 'other'},
                       {'com.docker.compose.project.working_dir': str(self.root)},
                       {'com.docker.compose.service': 'db'}):
            with self.subTest(change=change), patch.object(backup, 'run', return_value=json.dumps({**labels, **change}).encode()):
                with self.assertRaisesRegex(backup.BackupError, 'ownership'):
                    backup.owned_container(self.config, 'foreign-id')

    def test_duplicate_service_replica_refuses_inconsistent_capture(self):
        with patch.object(backup, 'run', return_value=b'id1\nid2\n'), patch.object(backup, 'owned_container', return_value='backend'):
            with self.assertRaisesRegex(backup.BackupError, 'single_instance'):
                backup.containers(self.config, backup.WRITERS)

    def test_failed_capture_restarts_exact_original_container_ids(self):
        running = {'backend': 'owned-backend', 'worker': 'owned-worker'}
        with patch.object(backup, 'containers', return_value=running), patch.object(backup, 'owned_container'), patch.object(backup, 'run') as command:
            with self.assertRaisesRegex(backup.BackupError, 'fixture_capture_failed'):
                with backup.stopped_writers(self.config):
                    raise backup.BackupError('fixture_capture_failed')
        self.assertEqual(command.call_args_list[0].args[0], ['docker', 'stop', '--time', '30', *running.values()])
        self.assertEqual(command.call_args_list[1].args[0], ['docker', 'start', *running.values()])
        self.assertFalse(self.config.maintenance.exists())

    def test_stale_checkpoint_requires_explicit_resume_and_validates_ownership(self):
        backup.write_json(self.config.maintenance, {'project': self.config.name, 'containers': ['owned']})
        with patch.object(backup, 'run') as command:
            with self.assertRaisesRegex(backup.BackupError, 'resume_first'):
                with backup.stopped_writers(self.config):
                    self.fail('must not enter capture')
            command.assert_not_called()
        with patch.object(backup, 'owned_container', side_effect=backup.BackupError('ownership')), patch.object(backup, 'run') as command:
            with self.assertRaises(backup.BackupError):
                backup.resume(self.config)
            command.assert_not_called()
        self.assertTrue(self.config.maintenance.exists())

    def test_roundtrip_checks_media_payment_keys_and_dump_bytes(self):
        bundle = self.root / 'bundle'
        bundle.mkdir()
        (bundle / 'database.dump').write_bytes(b'PGDMP-fixture')
        (bundle / 'media').mkdir()
        (bundle / 'media' / 'photo.jpg').write_bytes(b'merchant-original')
        (bundle / 'payment-secrets').mkdir()
        (bundle / 'payment-secrets' / 'wechat-accounts.json').write_text('{"fixture":true}')
        manifest = {'version': 1, 'tables': {'market_order': 'fixture'}, 'payments_configured': True,
                    'files': backup.file_manifest(bundle)}
        backup.write_json(bundle / backup.MANIFEST, manifest)
        self.assertEqual(backup.verify_files(bundle), manifest)
        for path in ['database.dump', 'media/photo.jpg', 'payment-secrets/wechat-accounts.json']:
            target = bundle / path
            previous = target.read_bytes()
            target.write_bytes(b'tampered')
            with self.subTest(path=path), self.assertRaisesRegex(backup.BackupError, 'mismatch'):
                backup.verify_files(bundle)
            target.write_bytes(previous)

    def test_rpo_uses_capture_time_not_late_upload_completion(self):
        backup.write_json(self.config.success, {'captured_at': (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
                                              'completed_at': backup.utcnow()})
        with self.assertRaisesRegex(backup.BackupError, 'rpo_target_exceeded'):
            backup.status(self.config)

    def test_restore_cannot_select_another_projects_snapshot(self):
        with patch.object(backup, 'restic', return_value=b'[{"id":"ours","time":"2026-10-03"}]'):
            self.assertEqual(backup.selected_snapshot(self.config, 'latest'), 'ours')
            with self.assertRaisesRegex(backup.BackupError, 'not_owned'):
                backup.selected_snapshot(self.config, 'someone-elses')

    def test_subprocess_failures_never_expose_credentials(self):
        result = SimpleNamespace(returncode=1, stdout=b'private-data', stderr=b'secret-password')
        with patch.object(backup.subprocess, 'run', return_value=result):
            with self.assertRaisesRegex(backup.BackupError, '^tool_returned_nonzero$'):
                backup.run(['fixture'])

    def test_restore_start_failure_only_removes_its_verified_container_and_volume(self):
        calls = []
        def command(args, **kwargs):
            calls.append(args)
            if args[:2] == ['docker', 'create']:
                self.assertIn('none', args)
                return b'owned-restore-id'
            if args[:2] == ['docker', 'start']:
                raise backup.BackupError('fixture_start_failure')
            if args[:2] == ['docker', 'inspect'] or args[:3] == ['docker', 'volume', 'inspect']:
                return b'fixture-run'
            return b''
        with patch.object(backup, 'selected_snapshot', return_value='owned-snapshot'), \
             patch.object(backup, 'restore_bundle', return_value=(self.root, {'tables': {}})), \
             patch.object(backup.secrets, 'token_hex', return_value='fixture-run'), \
             patch.object(backup, 'run', side_effect=command):
            with self.assertRaisesRegex(backup.BackupError, '^fixture_start_failure$'):
                backup.restore_check(self.config, 'latest')
        self.assertIn(['docker', 'rm', '--force', 'owned-restore-id'], calls)
        self.assertIn(['docker', 'volume', 'rm', 'yanhuo-restore-data-fixture-run'], calls)
        self.assertFalse((self.config.state / 'last-restore.json').exists())

    def test_restore_cleanup_refuses_an_ownership_mismatch(self):
        calls = []
        def command(args, **kwargs):
            calls.append(args)
            if args[:2] == ['docker', 'create']:
                return b'foreign-container-id'
            if args[:2] == ['docker', 'start']:
                raise backup.BackupError('fixture_start_failure')
            if args[:2] == ['docker', 'inspect']:
                return b'another-run'
            return b''
        with patch.object(backup, 'selected_snapshot', return_value='owned-snapshot'), \
             patch.object(backup, 'restore_bundle', return_value=(self.root, {'tables': {}})), \
             patch.object(backup.secrets, 'token_hex', return_value='fixture-run'), \
             patch.object(backup, 'run', side_effect=command):
            with self.assertRaisesRegex(backup.BackupError, 'ownership_mismatch'):
                backup.restore_check(self.config, 'latest')
        self.assertFalse(any(args[:2] == ['docker', 'rm'] or args[:3] == ['docker', 'volume', 'rm'] for args in calls))

    def test_restore_waits_for_final_tcp_server_before_loading_dump(self):
        calls = []
        readiness = 0
        def command(args, **kwargs):
            nonlocal readiness
            calls.append(args)
            if args[:2] == ['docker', 'create']:
                return b'owned-restore-id'
            if 'pg_isready' in args:
                self.assertIn('-h', args)
                self.assertEqual(args[args.index('-h') + 1], '127.0.0.1')
                readiness += 1
                if readiness == 1:
                    raise backup.BackupError('temporary_unix_server_not_ready_on_tcp')
            if args[:2] == ['docker', 'inspect'] or args[:3] == ['docker', 'volume', 'inspect']:
                return b'fixture-run'
            return b''
        with patch.object(backup, 'selected_snapshot', return_value='owned-snapshot'), \
             patch.object(backup, 'restore_bundle', return_value=(self.root, {'tables': {}, 'files': {}})), \
             patch.object(backup, 'fingerprint', return_value={}), \
             patch.object(backup.secrets, 'token_hex', return_value='fixture-run'), \
             patch.object(backup.time, 'sleep'), patch.object(backup, 'run', side_effect=command):
            backup.restore_check(self.config, 'latest')
        load_index = next(index for index, args in enumerate(calls) if 'pg_restore' in args)
        ready_indices = [index for index, args in enumerate(calls) if 'pg_isready' in args]
        self.assertEqual(len(ready_indices), 2)
        self.assertLess(ready_indices[-1], load_index)

    def test_dump_failure_reports_load_stage_without_raw_database_error(self):
        def command(args, **kwargs):
            if args[:2] == ['docker', 'create']:
                return b'owned-restore-id'
            if 'pg_restore' in args:
                raise backup.BackupError('tool_returned_nonzero')
            if args[:2] == ['docker', 'inspect'] or args[:3] == ['docker', 'volume', 'inspect']:
                return b'fixture-run'
            return b''
        with patch.object(backup, 'selected_snapshot', return_value='owned-snapshot'), \
             patch.object(backup, 'restore_bundle', return_value=(self.root, {'tables': {}, 'files': {}})), \
             patch.object(backup.secrets, 'token_hex', return_value='fixture-run'), \
             patch.object(backup, 'run', side_effect=command):
            with self.assertRaisesRegex(backup.BackupError, '^tool_returned_nonzero$'):
                backup.restore_check(self.config, 'latest')
        self.assertEqual(backup.STAGE, 'restore_load_dump')

    def test_failed_backup_persists_failure_even_when_webhook_fails(self):
        with patch.object(backup.sys, 'argv', ['backup.py', 'backup']), \
             patch.object(backup.os, 'environ', self.env), \
             patch.object(backup, 'backup', side_effect=backup.BackupError('fixture_failure')), \
             patch.object(backup, 'alert', side_effect=OSError('private-url-not-for-logs')):
            self.assertEqual(backup.main(), 1)
        state = json.loads((self.config.state / 'last-failure.json').read_text())
        self.assertEqual(state['reason'], 'fixture_failure')
        self.assertEqual(state['operation'], 'backup')
        self.assertFalse(state['alert_delivered'])
        self.assertIsNotNone(datetime.fromisoformat(state['failed_at']).tzinfo)

    def test_delivered_failure_alert_is_recorded_for_monitoring(self):
        with patch.object(backup.sys, 'argv', ['backup.py', 'backup']), \
             patch.object(backup.os, 'environ', self.env), \
             patch.object(backup, 'backup', side_effect=backup.BackupError('fixture_failure')), \
             patch.object(backup, 'alert'):
            self.assertEqual(backup.main(), 1)
        state = json.loads((self.config.state / 'last-failure.json').read_text())
        self.assertTrue(state['alert_delivered'])

    def test_restore_failure_survives_later_backup_failure_and_alert_retry(self):
        with patch.object(backup.sys, 'argv', ['backup.py', 'restore-check']), \
             patch.object(backup.os, 'environ', self.env), \
             patch.object(backup, 'restore_check', side_effect=backup.BackupError('restore_load_failed')), \
             patch.object(backup, 'alert', side_effect=OSError('receiver-unavailable')):
            self.assertEqual(backup.main(), 1)
        restore_path = self.config.state / 'last-restore-failure.json'
        first = json.loads(restore_path.read_text())
        self.assertEqual(first['operation'], 'restore-check')
        self.assertFalse(first['alert_delivered'])
        with patch.object(backup.sys, 'argv', ['backup.py', 'backup']), \
             patch.object(backup.os, 'environ', self.env), \
             patch.object(backup, 'backup', side_effect=backup.BackupError('capture_failed')), \
             patch.object(backup, 'alert'):
            self.assertEqual(backup.main(), 1)
        self.assertEqual(json.loads(restore_path.read_text()), first)
        self.assertEqual(json.loads((self.config.state / 'last-failure.json').read_text())['operation'], 'backup')

    def test_restore_failure_delivery_state_and_legacy_record_are_preserved(self):
        legacy = {'operation': 'restore-check', 'failed_at': backup.utcnow(),
                  'reason': 'legacy_restore_failed', 'alert_delivered': False}
        backup.write_json(self.config.state / 'last-failure.json', legacy)
        backup.record_failure(self.config, {'operation': 'status', 'failed_at': backup.utcnow(),
                                           'reason': 'backup_rpo_target_exceeded', 'alert_delivered': True})
        self.assertEqual(json.loads((self.config.state / 'last-restore-failure.json').read_text()), legacy)
        with patch.object(backup.sys, 'argv', ['backup.py', 'restore-check']), \
             patch.object(backup.os, 'environ', self.env), \
             patch.object(backup, 'restore_check', side_effect=backup.BackupError('another_restore_failure')), \
             patch.object(backup, 'alert'):
            self.assertEqual(backup.main(), 1)
        recorded = json.loads((self.config.state / 'last-restore-failure.json').read_text())
        self.assertTrue(recorded['alert_delivered'])
        self.assertEqual(recorded, json.loads((self.config.state / 'last-failure.json').read_text()))

    def test_failed_legacy_preservation_does_not_erase_restore_incident(self):
        legacy = {'operation': 'restore-check', 'failed_at': backup.utcnow(), 'reason': 'restore_failed'}
        latest = self.config.state / 'last-failure.json'
        backup.write_json(latest, legacy)
        with patch.object(backup, 'write_json', side_effect=OSError('storage_unavailable')):
            with self.assertRaises(OSError):
                backup.record_failure(self.config, {'operation': 'backup', 'failed_at': backup.utcnow()})
        self.assertEqual(json.loads(latest.read_text()), legacy)

    def test_database_identity_requires_explicit_valid_owned_configuration(self):
        invalid = [[], ['POSTGRES_USER=yanhuo'], ['POSTGRES_DB=yanhuo'],
                   ['POSTGRES_USER=yanhuo', 'POSTGRES_DB='],
                   ['POSTGRES_USER=--help', 'POSTGRES_DB=yanhuo'],
                   ['POSTGRES_USER=yanhuo', 'POSTGRES_DB=invalid;database'],
                   ['POSTGRES_USER=yanhuo', 'POSTGRES_DB=' + 'a' * 64],
                   ['POSTGRES_USER=yanhuo', 'POSTGRES_DB=first', 'POSTGRES_DB=second']]
        with patch.object(backup, 'owned_container') as owned:
            for entries in invalid:
                with self.subTest(entries=entries), patch.object(backup, 'run', return_value=json.dumps(entries).encode()):
                    with self.assertRaises(backup.BackupError):
                        backup.database_identity(self.config, 'owned-db')
            owned.assert_called_with(self.config, 'owned-db', ('db',))
        with patch.object(backup, 'owned_container', side_effect=backup.BackupError('ownership')), \
             patch.object(backup, 'run') as command:
            with self.assertRaisesRegex(backup.BackupError, 'ownership'):
                backup.database_identity(self.config, 'foreign-db')
            command.assert_not_called()

    def test_database_identity_selects_runtime_database_without_exposing_password(self):
        values = ['PATH=/usr/bin', 'POSTGRES_USER=yanhuo', 'POSTGRES_DB=yanhuo_runtime',
                  'POSTGRES_PASSWORD=synthetic-secret']
        with patch.object(backup, 'owned_container'), \
             patch.object(backup, 'run', return_value=json.dumps(values).encode()):
            self.assertEqual(backup.database_identity(self.config, 'owned-db'), ('yanhuo', 'yanhuo_runtime'))

    def test_capture_dump_and_fingerprint_use_the_same_verified_database(self):
        stopped = MagicMock()
        stopped.__enter__.return_value = {'backend': 'owned-backend'}

        def command(args, **kwargs):
            if args[:2] == ['docker', 'inspect']:
                return b'"owned-image"'
            if 'pg_dump' in args:
                kwargs['output'].write(b'PGDMP-synthetic')
            return b''

        def restic(config, *args, **kwargs):
            if args[0] == 'backup':
                return b'{"message_type":"summary","snapshot_id":"synthetic"}\n'
            return b'[]'

        with patch.object(backup, 'containers', return_value={'db': 'owned-db'}), \
             patch.object(backup, 'database_identity', return_value=('runtime_user', 'yanhuo_runtime')), \
             patch.object(backup, 'stopped_writers', return_value=stopped), \
             patch.object(backup, 'fingerprint', return_value={'table': 'synthetic'}) as fingerprint, \
             patch.object(backup, 'restic', side_effect=restic), \
             patch.object(backup, 'restore_bundle'), \
             patch.object(backup, 'run', side_effect=command) as run:
            backup.backup(self.config)
        fingerprint.assert_called_once_with('owned-db', 'runtime_user', 'yanhuo_runtime', row_counts={})
        dump = [call.args[0] for call in run.call_args_list if 'pg_dump' in call.args[0]]
        self.assertEqual(dump, [['docker', 'exec', 'owned-db', 'pg_dump', '-U', 'runtime_user',
                                 '-d', 'yanhuo_runtime', '-Fc']])

    def test_fingerprint_counts_nonempty_tables_without_storing_row_contents(self):
        rows = b'SET\n{"id":1,"note":"one\\ntwo"}\n{"id":2}\n'
        with patch.object(backup, 'run', side_effect=[b'market_order\nempty_table\n', rows, b'SET\n']):
            counts = {}
            hashes = backup.fingerprint('owned-db', 'yanhuo', 'yanhuo_runtime', row_counts=counts)
        self.assertEqual(counts, {'market_order': 2, 'empty_table': 0})
        self.assertEqual(hashes['market_order'], backup.hashlib.sha256(rows).hexdigest())


if __name__ == '__main__':
    unittest.main()
