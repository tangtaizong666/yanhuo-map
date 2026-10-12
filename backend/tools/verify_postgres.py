"""Verify official portable PostgreSQL/PostGIS builds without installing a service.

Place official PostgreSQL 17 Windows binaries at .runtime/postgresql.zip and the
PostGIS 3.6 Windows pg17 bundle at .runtime/postgis.zip. This script extracts only
runtime files into a new Temp directory, binds 127.0.0.1:55432, runs the real test
suite, verifies dump/restore, and always stops its own cluster. No existing DB is
modified. Password is random, passed through environment, and never printed.
"""
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import socket
import subprocess
import sys
import tempfile
import zipfile
from urllib.parse import quote

import psycopg

BASE = Path(__file__).resolve().parent.parent
TEMP = Path(tempfile.mkdtemp(prefix='yanhuo-postgres-check-'))
PG = TEMP / 'pgsql'
DATA = TEMP / 'data'
REPORT_DIR = BASE / 'test-results'
REPORT_DIR.mkdir(exist_ok=True)
REPORT = REPORT_DIR / ('postgres-concurrency.txt' if len(sys.argv)>1 else 'postgres-verification.txt')
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
env = os.environ.copy()
env['PYTHONIOENCODING'] = 'utf-8'
password = secrets.token_urlsafe(32)
env.update(PGHOST='127.0.0.1', PGPORT='55432', PGUSER='postgres', PGPASSWORD=password, PGSSLMODE='disable',
           DATABASE_URL=f'postgresql://postgres:{quote(password)}@127.0.0.1:55432/yanhuo_check',
           DB_SSLMODE='disable', DJANGO_ENV='development', DEMO_MODE='true', AMAP_KEY='', AMAP_SECURITY_CODE='')


def log(message):
    with REPORT.open('a', encoding='utf-8') as output: output.write(message+'\n')
    print(message, flush=True)


def run(args, timeout=180):
    # File-backed output avoids waiting forever on pipes inherited by pg_ctl's daemon.
    with tempfile.TemporaryFile() as output_file:
        try:
            result = subprocess.run([str(x) for x in args], env=env, cwd=BASE,
                stdout=output_file, stderr=output_file, timeout=timeout, creationflags=FLAGS)
        except subprocess.TimeoutExpired:
            output_file.seek(0)
            output = output_file.read().decode('utf-8', errors='replace')
            if password not in output: log(output.rstrip())
            raise
        output_file.seek(0)
        output = output_file.read().decode('utf-8', errors='replace')
    if password in output: raise RuntimeError('Refusing to print secret in process output')
    log(output.rstrip())
    if result.returncode: raise RuntimeError(f'{Path(args[0]).name} failed with exit code {result.returncode}')
    return output


def extract(zip_path, target):
    with zipfile.ZipFile(zip_path) as archive:
        count = 0
        for entry in archive.infolist():
            parts = PurePosixPath(entry.filename).parts
            if entry.is_dir() or len(parts)<3 or parts[1] not in ('bin', 'lib', 'share'): continue
            if '..' in parts: raise RuntimeError('Unsafe archive path')
            destination = target.joinpath(*parts[1:])
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(entry))
            count += 1
        log(f'Extracted {count} runtime files from {zip_path.name}')


def stats(database):
    with psycopg.connect(host='127.0.0.1', port=55432, user='postgres', password=password, dbname=database) as conn:
        with conn.cursor() as cursor:
            cursor.execute('SELECT postgis_version()')
            version = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM pg_indexes WHERE indexname='market_stalllocation_coordinates_gist'")
            indexes = cursor.fetchone()[0]
            results = {'postgis': version, 'spatial_index': indexes}
            for table in ['market_stall', 'market_product', 'market_order', 'market_orderitem', 'market_review']:
                cursor.execute(f'SELECT COUNT(*) FROM {table}')
                results[table] = cursor.fetchone()[0]
            cursor.execute('SELECT SUM(stock), SUM(price_cents) FROM market_product')
            results['stock_and_prices'] = list(cursor.fetchone())
            cursor.execute('SELECT SUM(total_cents) FROM market_order')
            results['order_total_cents'] = cursor.fetchone()[0]
            cursor.execute('SELECT COUNT(*) FROM market_stalllocation WHERE ST_IsValid(coordinates::geometry)')
            results['valid_locations'] = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM pg_constraint WHERE contype='f' AND NOT convalidated")
            results['unvalidated_foreign_keys'] = cursor.fetchone()[0]
            return results


def main():
    REPORT.write_text('Portable PostgreSQL/PostGIS verification\n', encoding='utf-8')
    with socket.socket() as port:
        port.bind(('127.0.0.1', 55432))  # Refuse to disturb an existing service.
    log(f'Isolated runtime: {TEMP}')
    extract(BASE / '.runtime/postgresql.zip', PG)
    extract(BASE / '.runtime/postgis.zip', PG)
    pwfile = TEMP / 'init-password'
    pwfile.write_text(password, encoding='ascii')
    run([PG/'bin/initdb.exe', '-D', DATA, '-U', 'postgres', '--encoding=UTF8', '--no-locale', '--auth=scram-sha-256', f'--pwfile={pwfile}'])
    pwfile.unlink()
    started = False
    try:
        started = True
        run([PG/'bin/pg_ctl.exe', '-D', DATA, '-l', TEMP/'postgres.log', '-o', '-h 127.0.0.1 -p 55432', '-w', 'start'])
        run([PG/'bin/createdb.exe', 'yanhuo_check'])
        run([sys.executable, 'manage.py', 'migrate', '--noinput'])
        # The full suite includes password hashing and multi-process contention;
        # allow it to finish on developer laptops as well as CI runners.
        run([sys.executable, 'manage.py', 'test', *(sys.argv[1:] or ['market']), '--verbosity', '2', '--noinput'], timeout=1200)
        run([sys.executable, 'manage.py', 'seed_demo'])
        smoke = """from django.contrib.auth.models import User
from market.models import Stall, Product
from market.services import create_order, merchant_action
import uuid
u=User.objects.get(username='student'); v=User.objects.get(username='vendor')
s=Stall.objects.get(name='老李烤冷面'); p=s.products.first()
o,_=create_order(u,{'stall_id':s.pk,'items':[{'product_id':p.pk,'quantity':2,'expected_price_cents':p.price_cents}],'note':'恢复演练','contact_phone':'','idempotency_key':str(uuid.uuid4())})
for action in ['accept','ready','confirm_payment','complete']: merchant_action(o.pk,v,action,o.pickup_code)
print('Disposable completed order ready for backup verification.')"""
        run([sys.executable, 'manage.py', 'shell', '-c', smoke])
        dump = TEMP/'verified.dump'
        run([PG/'bin/pg_dump.exe', '--format=custom', '--file', dump, 'yanhuo_check'])
        run([PG/'bin/createdb.exe', 'yanhuo_restore'])
        run([PG/'bin/pg_restore.exe', '--exit-on-error', '--no-owner', '--dbname=yanhuo_restore', dump])
        original, restored = stats('yanhuo_check'), stats('yanhuo_restore')
        if original != restored: raise RuntimeError('Backup restoration statistics mismatch')
        if original['spatial_index'] != 1 or original['unvalidated_foreign_keys'] != 0:
            raise RuntimeError('Spatial index or foreign-key validation failed')
        log('BACKUP_RESTORE_MATCH '+json.dumps(restored, ensure_ascii=False))
        log('PASS: PostgreSQL tests, PostGIS generated geography/GiST, completed order, pg_dump and pg_restore.')
    finally:
        if started: run([PG/'bin/pg_ctl.exe', '-D', DATA, '-m', 'fast', '-w', 'stop'])


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    main()
