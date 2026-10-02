"""Run isolated PostgreSQL tests using existing portable binaries; no seed/reset.

The supplied directory is read-only runtime input. A fresh temporary data directory
is created, bound only to loopback, and always stopped. Artifacts remain for review.
"""
import argparse
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
from urllib.parse import quote


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bin', required=True, type=Path)
    parser.add_argument('--port', type=int, default=55432)
    parser.add_argument('--report', type=Path, help='Optional report filename; existing reports are preserved when a new name is used.')
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
        SERVICES_SIMULATION_ENABLED='false', PYTHONIOENCODING='utf-8', AMAP_KEY='', AMAP_SECURITY_CODE='',
        DATABASE_URL=f'postgresql://postgres:{quote(password)}@127.0.0.1:{args.port}/yanhuo_operations_check')
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    report = args.report or workspace / 'test-results' / 'operations-20260929-postgres.txt'
    report.parent.mkdir(exist_ok=True)
    with report.open('w', encoding='utf-8') as output:
        def log(message):
            output.write(message + '\n')
            output.flush()
            print(message, flush=True)
        def run(command, timeout=240):
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
            log('PASS: isolated PostgreSQL tests. No live database migration, seed or reset.')
        finally:
            if started:
                run([runtime/'pg_ctl.exe', '-D', data, '-m', 'fast', '-w', 'stop'], timeout=60)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    main()
