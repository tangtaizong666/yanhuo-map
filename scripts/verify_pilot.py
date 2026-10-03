"""Reproducible isolated checks. Never starts or seeds the working demo database."""
import argparse
from datetime import datetime
import os
from pathlib import Path, PurePosixPath
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / 'backend'
FRONTEND = ROOT / 'frontend'
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def extract_runtime():
    destination = Path(tempfile.mkdtemp(prefix='yanhuo-pilot-pg-runtime-')).resolve()
    for name in ('postgresql.zip', 'postgis.zip'):
        archive_path = BACKEND / '.runtime' / name
        if not archive_path.is_file():
            raise RuntimeError(f'Missing portable runtime {archive_path}; see backend/tools/verify_postgres.py.')
        with zipfile.ZipFile(archive_path) as archive:
            for entry in archive.infolist():
                parts = PurePosixPath(entry.filename).parts
                if entry.is_dir() or len(parts) < 3 or parts[1] not in ('bin', 'lib', 'share'):
                    continue
                target = destination.joinpath(*parts[1:]).resolve()
                if '..' in parts or not target.is_relative_to(destination):
                    raise RuntimeError('Unsafe runtime archive path')
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, target.open('wb') as output:
                    shutil.copyfileobj(source, output)
    return destination / 'bin'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--postgres', action='store_true', help='Run all backend tests on a disposable PostgreSQL/PostGIS cluster.')
    parser.add_argument('--skip-browser', action='store_true')
    options = parser.parse_args()
    artifacts = ROOT / '.runtime' / ('pilot-verification-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    artifacts.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy()
    env.update(DJANGO_ENV='development', DEMO_MODE='true', SERVICES_SIMULATION_ENABLED='false',
        DATABASE_URL='', WECHAT_PAY_ENABLED='false', WECHAT_PAY_CONFIG_FILE='',
        AMAP_KEY='', AMAP_SECURITY_CODE='', PYTHONIOENCODING='utf-8')
    env.pop('E2E_PRODUCTION', None)
    node = shutil.which('node')
    if not node:
        raise RuntimeError('Node.js is required.')

    def run(name, command, cwd, timeout=1200):
        print(f'Running {name} ...', flush=True)
        with (artifacts / (name + '.log')).open('w', encoding='utf-8') as output:
            result = subprocess.run([str(arg) for arg in command], cwd=cwd, env=env,
                stdout=output, stderr=subprocess.STDOUT, creationflags=FLAGS, timeout=timeout)
        if result.returncode:
            raise RuntimeError(f'{name} failed; see {artifacts / (name + ".log")}')
        print(f'PASS {name}', flush=True)

    print(f'Artifacts: {artifacts}', flush=True)
    run('django-check', [sys.executable, 'manage.py', 'check'], BACKEND)
    run('migration-check', [sys.executable, 'manage.py', 'makemigrations', '--check', '--dry-run'], BACKEND)
    if options.postgres:
        runtime = extract_runtime()
        run('postgres-tests', [sys.executable, 'tools/verify_operations.py', '--bin', runtime,
            '--report', artifacts/'postgres-details.log', '--restore', 'market'], BACKEND)
    else:
        run('sqlite-tests', [sys.executable, 'manage.py', 'test', 'market', '--noinput'], BACKEND)
    run('typecheck', [node, 'node_modules/vue-tsc/bin/vue-tsc.js', '-b'], FRONTEND)
    run('frontend-build', [node, 'node_modules/vite/bin/vite.js', 'build'], FRONTEND)
    if options.skip_browser:
        return
    def browser_run(name, port, specs, *, production=False):
        # These ports belong only to disposable fixture servers. Refuse collisions.
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', port))
        env['E2E_BASE_URL'] = f'http://127.0.0.1:{port}'
        if production: env['E2E_PRODUCTION'] = '1'
        with (artifacts/(name+'-server.log')).open('w', encoding='utf-8') as output:
            server = subprocess.Popen([node, 'node_modules/vite/bin/vite.js',
                *(['preview'] if production else []), '--host', '127.0.0.1', '--port', str(port), '--strictPort'],
                cwd=FRONTEND, env=env, stdout=output, stderr=subprocess.STDOUT, creationflags=FLAGS)
            try:
                for _ in range(60):
                    if server.poll() is not None: raise RuntimeError('Fixture server exited.')
                    try:
                        with urllib.request.urlopen(env['E2E_BASE_URL'], timeout=1): break
                    except OSError: time.sleep(0.25)
                else: raise RuntimeError('Fixture server did not become ready.')
                run(name, [node, 'node_modules/@playwright/test/cli.js', 'test', *specs,
                    '--reporter=list', f'--output={artifacts / name}'], FRONTEND)
            finally:
                server.terminate()
                try: server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill(); server.wait(timeout=10)
    browser_run('browser-fixtures', 5193, ['e2e/api-resilience.spec.ts', 'e2e/checkout-recovery.spec.ts',
        'e2e/merchant-delivery-drafts.spec.ts', 'e2e/pilot-reliability.spec.ts'])
    browser_run('browser-production', 5194,
        ['e2e/pilot-reliability.spec.ts', '-g', 'production startup'], production=True)
    run('browser-api-integration', [sys.executable, ROOT/'scripts/verify_browser_integration.py'], ROOT)
    print('PASS: isolated pilot checks; real WeChat and physical devices require separate acceptance.', flush=True)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    main()
