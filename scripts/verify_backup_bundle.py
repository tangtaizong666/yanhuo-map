"""Exercise real restic encryption/restore with fake files in a disposable local repo.

This validates the bundle transport, not offsite durability or PostgreSQL recovery.
The production backup command still rejects local repositories.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import tempfile

import backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restic', default='restic')
    args = parser.parse_args()
    executable = str(Path(args.restic).resolve()) if Path(args.restic).is_file() else args.restic
    with tempfile.TemporaryDirectory(prefix='yanhuo-restic-fixture-') as directory:
        root = Path(directory)
        bundle = root / 'bundle'
        bundle.mkdir()
        (bundle / 'media').mkdir()
        (bundle / 'payment-secrets').mkdir()
        (bundle / 'database.dump').write_bytes(b'fixture-only-not-a-real-postgresql-dump')
        (bundle / 'media' / 'merchant.jpg').write_bytes(b'fixture-original-photo-bytes')
        (bundle / 'payment-secrets' / 'wechat-accounts.json').write_text('{"fixture_only":true}', encoding='utf-8')
        (bundle / 'deployment.env').write_text('FIXTURE_ONLY=true', encoding='utf-8')
        backup.write_json(bundle / backup.MANIFEST, {'version': 1, 'tables': {'fixture': 'not-a-database'},
            'payments_configured': True, 'files': backup.file_manifest(bundle)})
        password = root / 'password'
        password.write_text(secrets.token_hex(32), encoding='utf-8')
        if os.name != 'nt': password.chmod(0o600)
        env = {**os.environ, 'RESTIC_REPOSITORY': str(root / 'repository'), 'RESTIC_PASSWORD_FILE': str(password),
            'RESTIC_CACHE_DIR': str(root / 'cache')}
        # Explicit fixture-only local initialization; production never does this.
        backup.run([executable, 'init'], env=env)
        lines = backup.run([executable, 'backup', '--json', '--tag', 'yanhuo-fixture', '--host', 'fixture', '.'],
            cwd=bundle, env=env).decode().splitlines()
        summary = [json.loads(line) for line in lines if json.loads(line).get('message_type') == 'summary']
        assert len(summary) == 1 and summary[0]['snapshot_id']
        restored = root / 'restored'
        backup.run([executable, 'restore', summary[0]['snapshot_id'], '--target', restored, '--verify'], env=env)
        manifests = list(restored.rglob(backup.MANIFEST))
        assert len(manifests) == 1
        backup.verify_files(manifests[0].parent)
        backup.run([executable, 'check'], env=env)
        backup.run([executable, 'forget', '--prune', '--group-by', 'host,tags', '--host', 'fixture',
            '--tag', 'yanhuo-fixture', '--keep-hourly', '48', '--keep-daily', '14', '--keep-weekly', '8', '--keep-monthly', '12'], env=env)
        wrong = root / 'wrong-password'
        wrong.write_text(secrets.token_hex(32), encoding='utf-8')
        try:
            backup.run([executable, 'snapshots', '--json'], env={**env, 'RESTIC_PASSWORD_FILE': str(wrong)})
        except backup.BackupError:
            pass
        else:
            raise AssertionError('An unrelated password opened the encrypted fixture repository')
    print(json.dumps({'status': 'passed', 'fixture_only': True, 'database_restore_tested': False,
        'checks': ['encrypted-backup', 'verified-restore-manifest', 'repository-check', 'retention', 'wrong-password-rejected']}))


if __name__ == '__main__':
    main()
