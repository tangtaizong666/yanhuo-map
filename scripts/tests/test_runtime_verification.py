"""Disposable deployment runner's destructive boundary and failure evidence."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('runtime_verification', Path(__file__).resolve().parents[1] / 'verify_runtime.py')
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


def result(stdout='', stderr='', returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


class RuntimeVerificationSafetyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.run = runtime.Verification(self.root / 'evidence')
        self.addCleanup(lambda: shutil.rmtree(self.run.directory, ignore_errors=True))
        self.labels = {
            runtime.RUN_LABEL: self.run.run_id,
            'com.docker.compose.project': self.run.project,
            'com.docker.compose.project.working_dir': str(self.run.directory),
            'com.docker.compose.service': 'backend',
        }

    def test_timeout_preserves_partial_output_and_redacts_generated_credentials(self):
        secret = self.run.secrets[0]
        failure = subprocess.TimeoutExpired('fixture', 12, output=('before ' + secret).encode(), stderr=b'partial error')
        with patch.object(runtime.subprocess, 'run', side_effect=failure):
            with self.assertRaisesRegex(RuntimeError, 'partial output saved'):
                self.run.command('timed-out-step', ['fixture'], timeout=12)
        log = (self.run.evidence / 'timed-out-step.log').read_text()
        self.assertIn('before ', log)
        self.assertIn('partial error', log)
        self.assertIn('[disposable-secret-redacted]', log)
        self.assertNotIn(secret, log)

    def test_failed_command_writes_diagnostic_before_raising(self):
        secret = self.run.secrets[1]
        with patch.object(runtime.subprocess, 'run', return_value=result('fixture started', secret, 9)):
            with self.assertRaisesRegex(RuntimeError, 'exit 9'):
                self.run.command('failed-step', ['fixture'])
        log = (self.run.evidence / 'failed-step.log').read_text()
        self.assertIn('fixture started', log)
        self.assertNotIn(secret, log)

    def test_inspect_rejects_wrong_run_project_directory_or_service(self):
        for key, value in [(runtime.RUN_LABEL, 'foreign-run'),
                           ('com.docker.compose.project', 'foreign-project'),
                           ('com.docker.compose.project.working_dir', str(self.root / 'foreign')),
                           ('com.docker.compose.service', 'db')]:
            info = [{'Id': 'candidate-id', 'Config': {'Labels': {**self.labels, key: value}}}]
            with self.subTest(key=key), patch.object(runtime.subprocess, 'run', side_effect=[
                    result('candidate-id\n'), result(json.dumps(info))]):
                with self.assertRaisesRegex(RuntimeError, 'ownership mismatch'):
                    self.run.inspect('backend')

    def test_inspect_requires_one_container_not_arbitrary_replica(self):
        for ids in ('', 'first\nsecond\n'):
            with self.subTest(ids=ids), patch.object(runtime.subprocess, 'run', return_value=result(ids)) as command:
                with self.assertRaisesRegex(RuntimeError, 'Expected one owned'):
                    self.run.inspect('backend')
                self.assertEqual(command.call_count, 1)

    def test_cleanup_rejects_changed_run_or_project_labels_without_deleting(self):
        for kind in ('container', 'volume', 'network'):
            for changed in ({runtime.RUN_LABEL: 'foreign-run'}, {'com.docker.compose.project': 'foreign-project'}):
                labels = {**self.labels, **changed}
                info = {'Config': {'Labels': labels}} if kind == 'container' else {'Labels': labels}
                before = {'container': [], 'volume': [result('')], 'network': [result(''), result('')]}[kind]
                with self.subTest(kind=kind, changed=changed), \
                     patch.object(runtime.subprocess, 'run', side_effect=[*before, result('candidate-id'), result(json.dumps([info]))]), \
                     patch.object(self.run, 'command') as mutation:
                    with self.assertRaisesRegex(RuntimeError, 'Cleanup ownership mismatch'):
                        self.run.cleanup()
                    mutation.assert_not_called()
                self.assertTrue(self.run.directory.exists())

    def test_cleanup_rejects_container_from_another_working_directory(self):
        labels = {**self.labels, 'com.docker.compose.project.working_dir': str(self.root / 'not-this-run')}
        with patch.object(runtime.subprocess, 'run', side_effect=[
                result('candidate-id'), result(json.dumps([{'Config': {'Labels': labels}}]))]), \
             patch.object(self.run, 'command') as mutation:
            with self.assertRaisesRegex(RuntimeError, 'Cleanup directory mismatch'):
                self.run.cleanup()
            mutation.assert_not_called()

    def test_cleanup_removes_only_ids_returned_under_exact_run_filter(self):
        calls = []
        responses = [result('owned-container'), result(json.dumps([{'Config': {'Labels': self.labels}}])),
                     result('owned-volume'), result(json.dumps([{'Labels': self.labels}])),
                     result('owned-network'), result(json.dumps([{'Labels': self.labels}]))]
        with patch.object(runtime.subprocess, 'run', side_effect=responses) as reads, \
             patch.object(self.run, 'command', side_effect=lambda name, args, **kwargs: calls.append(args)):
            self.run.cleanup()
        for call in reads.call_args_list[::2]:
            self.assertIn('label=' + runtime.RUN_LABEL + '=' + self.run.run_id, call.args[0])
        self.assertEqual(calls, [['docker', 'rm', '--force', 'owned-container'],
                                ['docker', 'volume', 'rm', 'owned-volume'],
                                ['docker', 'network', 'rm', 'owned-network']])
        self.assertEqual(self.run.report['cleanup']['status'], 'passed')
        self.assertFalse(self.run.directory.exists())

    def test_image_tag_reassignment_prevents_image_deletion(self):
        self.run.images['yanhuo-verification-backend:' + self.run.run_id] = 'original-image-id'
        with patch.object(runtime.subprocess, 'run', side_effect=[result(''), result(''), result(''), result('other-image-id')]), \
             patch.object(self.run, 'command') as mutation:
            with self.assertRaisesRegex(RuntimeError, 'Image tag ownership changed'):
                self.run.cleanup()
            mutation.assert_not_called()
        self.assertTrue(self.run.directory.exists())


if __name__ == '__main__':
    unittest.main()
