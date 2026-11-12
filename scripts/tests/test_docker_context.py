"""Use Docker's own ignore matcher against synthetic credentials and data."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(shutil.which('docker'), 'Docker CLI is unavailable')
class DockerContextIsolationTests(unittest.TestCase):
    def test_local_configuration_credentials_and_database_files_never_enter_context(self):
        probe = subprocess.run(['docker', 'info'], capture_output=True, timeout=30)
        if probe.returncode:
            self.skipTest('Docker daemon is unavailable')
        ignored = [
            '.env', '.env.local', '.env.production', 'backend/.env.local',
            'frontend/.env.development.local', 'backend/nested/.env.test',
            'backend/db.sqlite3', 'backend/db.sqlite3-wal', 'backend/db.sqlite3-shm',
            'backend/database.dump', 'backend/database.backup', 'backend/debug.log',
            'backend/payment-secrets/account.json', 'backend/wechat-accounts.json',
            'backend/api-v3-key-test.txt', 'backend/private.pem', 'backend/private.key',
            'backend/media/customer.png', '.runtime/processes.json',
        ]
        allowed = ['backend/config/settings.py', 'frontend/src/main.ts', 'deploy/Caddyfile']
        with tempfile.TemporaryDirectory(prefix='yanhuo-build-context-') as temporary:
            root = Path(temporary)
            context, output = root / 'context', root / 'output'
            context.mkdir()
            for name in ignored + allowed:
                file = context / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text('synthetic-build-context-fixture-only', encoding='utf-8')
            source = Path(__file__).resolve().parents[2] / '.dockerignore'
            shutil.copyfile(source, context / '.dockerignore')
            (context / 'Dockerfile').write_text('FROM scratch\nCOPY . /\n', encoding='utf-8')
            result = subprocess.run(['docker', 'build', '--network=none', '--progress=plain',
                '--output', f'type=local,dest={output}', str(context)],
                capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stderr[-4000:])
            for name in ignored:
                with self.subTest(excluded=name):
                    self.assertFalse((output / name).exists(), f'Private fixture entered the image: {name}')
            for name in allowed:
                with self.subTest(included=name):
                    self.assertTrue((output / name).is_file(), f'Application source excluded: {name}')


if __name__ == '__main__':
    unittest.main()
