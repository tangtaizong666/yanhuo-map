"""Opt-in real SFTP/restic regression; only labelled disposable resources.

Run on Linux with YANHUO_TEST_SFTP=1. A root runner deliberately gives the
mounted public key an unrelated UID to reproduce GitHub's host/container gap.
Ordinary script-unit runs skip Docker; full runtime CI also exercises this path.
"""
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    'sftp_transport_verify', Path(__file__).resolve().parents[1] / 'verify_runtime_backup.py')
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)


@unittest.skipUnless(os.name == 'posix' and os.environ.get('YANHUO_TEST_SFTP') == '1',
                     'Opt-in Docker/SFTP transport integration')
class SftpHostOwnershipTests(unittest.TestCase):
    def test_unrelated_host_uid_authenticates_without_disabling_strict_modes(self):
        for tool in ('docker', 'restic', 'ssh', 'ssh-keygen'):
            self.assertIsNotNone(shutil.which(tool), tool + ' is required')
        with tempfile.TemporaryDirectory(prefix='yanhuo-sftp-uid-regression-') as temporary:
            directory = Path(temporary)
            directory.chmod(0o700)
            evidence = directory / 'evidence'
            evidence.mkdir()
            report = {'status': 'running', 'transport': 'real_sftp_restic',
                      'strict_host_key_checking': True, 'password_authentication': False}
            cleanup = {'auxiliary_resources_removed': False}
            original_command = verify.command

            def with_unrelated_public_key_owner(args, **kwargs):
                result = original_command(args, **kwargs)
                if args[0] == 'ssh-keygen' and Path(args[-1]).name == 'client':
                    public = Path(str(args[-1]) + '.pub')
                    if os.geteuid() == 0:
                        # Match a GitHub runner-owned bind mount, while the
                        # container's SFTP login UID is independently 10001.
                        os.chown(public, 1001, 1001)
                    report['mounted_public_key_uid'] = public.stat().st_uid
                    self.assertNotIn(public.stat().st_uid, (0, 10001))
                return result

            try:
                with patch.object(verify, 'command', side_effect=with_unrelated_public_key_owner):
                    with verify.sftp_fixture(directory, secrets.token_hex(8), evidence, cleanup) as (identifier, ssh):
                        password = verify.private_write(directory / 'restic-password', secrets.token_urlsafe(32))
                        env = {key: value for key, value in os.environ.items() if not key.startswith('RESTIC_')}
                        env.update(RESTIC_REPOSITORY='sftp:yanhuo_backup@127.0.0.1:/repository',
                                   RESTIC_PASSWORD_FILE=str(password))
                        restic = [shutil.which('restic'), '-o', 'sftp.command=' + shlex.join(ssh)]
                        for attempt in range(20):
                            initialized = subprocess.run([*restic, 'init'], env=env,
                                                         capture_output=True, text=True, timeout=30)
                            if initialized.returncode == 0:
                                break
                            time.sleep(0.25)
                        self.assertEqual(initialized.returncode, 0, initialized.stderr)
                        ownership = original_command(['docker', 'exec', identifier, 'stat', '-c', '%u %a',
                            '/etc/ssh/authorized_keys/yanhuo_backup'])
                        effective = original_command(['docker', 'exec', identifier, '/usr/sbin/sshd', '-T',
                            '-f', '/fixture/sshd_config'])
                        self.assertEqual(ownership, '0 644')
                        self.assertIn('strictmodes yes', effective)
                        self.assertIn('passwordauthentication no', effective)
                        report['authorized_key'] = {'uid': 0, 'mode': '0644', 'strict_modes': True}
                        source = verify.private_write(directory / 'roundtrip.txt', 'isolated SFTP UID regression\n')
                        original_command([*restic, 'backup', str(source)], env=env)
                        restored = original_command([*restic, 'dump', 'latest', str(source)], env=env)
                        self.assertEqual(restored, source.read_text().strip())
                        report['encrypted_roundtrip'] = 'passed'
                        report['status'] = 'passed'
            finally:
                report['cleanup'] = cleanup
                output = os.environ.get('YANHUO_TEST_SFTP_REPORT')
                if output:
                    Path(output).write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
            self.assertTrue(cleanup['auxiliary_resources_removed'])


if __name__ == '__main__':
    unittest.main()
