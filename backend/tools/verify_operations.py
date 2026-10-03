"""Run isolated PostgreSQL tests using existing portable binaries; no seed/reset.

The supplied directory is read-only runtime input. A fresh temporary data directory
is created, bound only to loopback, and always stopped. Artifacts remain for review.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import tarfile
from urllib.parse import quote


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bin', required=True, type=Path)
    parser.add_argument('--port', type=int, default=55432)
    parser.add_argument('--report', type=Path, help='Optional report filename; existing reports are preserved when a new name is used.')
    parser.add_argument('--restore', action='store_true', help='Also verify a disposable DB/media/encrypted payment-config backup round trip.')
    parser.add_argument('labels', nargs='+')
    args = parser.parse_args()
    runtime = args.bin.resolve(strict=True)
    for executable in ('initdb.exe', 'pg_ctl.exe', 'createdb.exe'):
        if not (runtime / executable).is_file(): parser.error(f'Runtime is missing {executable}')
    if not 1024 <= args.port <= 65535: parser.error('Port must be between 1024 and 65535')
    with socket.socket() as probe: probe.bind(('127.0.0.1', args.port))
    workspace = Path(__file__).resolve().parent.parent
    temporary = Path(tempfile.mkdtemp(prefix='yanhuo-operations-pg-')).resolve()
    data = temporary / 'data'
    if data.exists() or not data.is_relative_to(temporary): raise RuntimeError('Expected new isolated data directory')
    password = secrets.token_urlsafe(32)
    env = os.environ.copy()
    env.update(PGHOST='127.0.0.1', PGPORT=str(args.port), PGUSER='postgres', PGPASSWORD=password,
        PGSSLMODE='disable', DB_SSLMODE='disable', DJANGO_ENV='development', DEMO_MODE='true',
        SERVICES_SIMULATION_ENABLED='false', WECHAT_PAY_ENABLED='false', WECHAT_PAY_CONFIG_FILE='',
        PYTHONIOENCODING='utf-8', AMAP_KEY='', AMAP_SECURITY_CODE='',
        DATABASE_URL=f'postgresql://postgres:{quote(password)}@127.0.0.1:{args.port}/yanhuo_operations_check')
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    report = args.report or workspace / 'test-results' / 'operations-20260929-postgres.txt'
    report.parent.mkdir(exist_ok=True)
    with report.open('w', encoding='utf-8') as output:
        def log(message):
            output.write(message + '\n')
            output.flush()
            print(message, flush=True)
        def run(command, timeout=1200):
            # File output does not leave pipe handles inherited by the server.
            with tempfile.TemporaryFile() as capture:
                result = subprocess.run([str(value) for value in command], env=env, cwd=workspace,
                    stdout=capture, stderr=capture, timeout=timeout, creationflags=flags)
                capture.seek(0)
                text = capture.read().decode('utf-8', errors='replace')
            if password in text: raise RuntimeError('Refusing to output a secret')
            log(text.rstrip())
            if result.returncode: raise RuntimeError(f'{Path(command[0]).name} failed ({result.returncode})')
        log(f'Runtime (read only): {runtime}\nIsolated cluster: {temporary}\nLabels: {args.labels}')
        password_file = temporary / 'init-password'
        password_file.write_text(password, encoding='ascii')
        run([runtime/'initdb.exe', '-D', data, '-U', 'postgres', '--encoding=UTF8', '--no-locale',
             '--auth=scram-sha-256', f'--pwfile={password_file}'])
        password_file.unlink()
        started = False
        try:
            started = True
            run([runtime/'pg_ctl.exe', '-D', data, '-l', temporary/'postgres.log',
                 '-o', f'-h 127.0.0.1 -p {args.port}', '-w', 'start'])
            run([runtime/'createdb.exe', 'yanhuo_operations_check'])
            run([sys.executable, 'manage.py', 'test', *args.labels, '--verbosity', '2', '--noinput'])
            if args.restore:
                # Only the database just created by this script is seeded.
                run([sys.executable, 'manage.py', 'migrate', '--noinput'])
                run([sys.executable, 'manage.py', 'seed_demo'])
                run([sys.executable, 'manage.py', 'shell', '-c', 'from tools.backup_fixture import seed_restore_fixture; seed_restore_fixture()'])
                dump = temporary / 'database.dump'
                run([runtime/'pg_dump.exe', '--format=custom', '--file', dump, 'yanhuo_operations_check'])
                run([runtime/'createdb.exe', 'yanhuo_restore_check'])
                run([runtime/'pg_restore.exe', '--exit-on-error', '--no-owner', '--dbname=yanhuo_restore_check', dump])
                import psycopg
                def snapshot(database):
                    with psycopg.connect(host='127.0.0.1', port=args.port, user='postgres', password=password, dbname=database) as conn:
                        with conn.cursor() as cursor:
                            cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' AND (tablename LIKE 'market_%' OR tablename LIKE 'auth_%' OR tablename = 'django_migrations') ORDER BY tablename")
                            names = [row[0] for row in cursor.fetchall()]
                            result = {}
                            for name in names:
                                cursor.execute(psycopg.sql.SQL('SELECT to_jsonb(t)::text FROM {} t ORDER BY to_jsonb(t)::text').format(psycopg.sql.Identifier(name)))
                                result[name] = hashlib.sha256(json.dumps(cursor.fetchall()).encode()).hexdigest()
                            return result
                if snapshot('yanhuo_operations_check') != snapshot('yanhuo_restore_check'):
                    raise RuntimeError('Database restore differs from the original snapshot')
                media = temporary / 'fixture-media'
                media.mkdir()
                (media/'merchant-photo.bin').write_bytes(b'isolated-media-backup-fixture')
                archive_path = temporary / 'media.tar.gz'
                with tarfile.open(archive_path, 'w:gz') as archive:
                    archive.add(media, arcname='media')
                restored = temporary / 'restored-media'
                restored.mkdir()
                with tarfile.open(archive_path) as archive:
                    archive.extractall(restored, filter='data')
                if (restored/'media/merchant-photo.bin').read_bytes() != (media/'merchant-photo.bin').read_bytes():
                    raise RuntimeError('Media restore mismatch')
                from cryptography.fernet import Fernet
                cipher = Fernet(Fernet.generate_key())
                config_fixture = b'{"fixture_only":true,"accounts":[]}'
                protected = temporary/'payment-config.enc'
                protected.write_bytes(cipher.encrypt(config_fixture))
                if cipher.decrypt(protected.read_bytes()) != config_fixture:
                    raise RuntimeError('Encrypted payment-config restore mismatch')
                log('PASS: pg_dump/pg_restore table-content hashes match; media bytes and encrypted dummy payment-config round trip match. No real payment keys used.')
            log('PASS: isolated PostgreSQL tests. No live database migration, seed or reset.')
        finally:
            if started:
                run([runtime/'pg_ctl.exe', '-D', data, '-m', 'fast', '-w', 'stop'], timeout=60)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    main()
