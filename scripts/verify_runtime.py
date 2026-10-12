"""Disposable production-stack acceptance for Linux, WSL and CI.

Run: python3 scripts/verify_runtime.py
Windows: powershell -File scripts/verify-runtime.ps1
No production env file, payment credentials, host trust store or existing ports
are used. Every Docker resource is checked by label and ID before deletion.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parent.parent
WRITERS = ('backend', 'callback', 'worker', 'payment_worker', 'notification_worker')
RUN_LABEL = 'yanhuo.verification.run'


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


class Verification:
    def __init__(self, evidence):
        self.run_id = uuid.uuid4().hex[:12]
        self.project = 'yanhuo-verify-' + self.run_id
        self.evidence = evidence.resolve()
        self.evidence.mkdir(parents=True, exist_ok=False)
        self.directory = Path(tempfile.mkdtemp(prefix='yanhuo-runtime-')).resolve()
        self.config_path = self.directory / 'compose.yaml'
        self.env_file = self.directory / 'runtime.env'
        self.env_file.write_text('', encoding='utf-8')
        self.env_file.chmod(0o600)
        self.secrets = [secrets.token_hex(24), secrets.token_hex(48)]
        self.images = {}
        self.report = {'run_id': self.run_id, 'project': self.project, 'started_at': time.time(),
                       'synthetic_only': True, 'database': 'PostgreSQL 17 / PostGIS 3.5',
                       'real_payment': False, 'stages': {}, 'evidence': str(self.evidence),
                       'orchestrator_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                       'host_tools': {}}
        save_json(self.directory / '.yanhuo-runtime-verification.json', {
            'project_name': self.project, 'synthetic_only': True, 'run_id': self.run_id})
        self.base = ['docker', 'compose', '--project-directory', str(self.directory),
                     '--project-name', self.project, '--env-file', str(self.env_file),
                     '-f', str(self.config_path)]

    def command(self, name, args, *, timeout=600, check=True, data=None, env=None):
        print(f'[{self.project}] {name}', flush=True)
        try:
            result = subprocess.run(list(map(str, args)), input=data, text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    timeout=timeout, env=env)
        except subprocess.TimeoutExpired as exc:
            def decoded(value):
                return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else value or ''
            log = decoded(exc.stdout) + '\n' + decoded(exc.stderr)
            for secret in self.secrets:
                log = log.replace(secret, '[disposable-secret-redacted]')
            (self.evidence / (name + '.log')).write_text(log, encoding='utf-8')
            raise RuntimeError(f'{name}: timeout after {timeout}s; partial output saved') from exc
        log = result.stdout + '\n' + result.stderr
        for secret in self.secrets:
            log = log.replace(secret, '[disposable-secret-redacted]')
        (self.evidence / (name + '.log')).write_text(log, encoding='utf-8')
        if check and result.returncode:
            raise RuntimeError(f'{name}: exit {result.returncode}; see {name}.log')
        return result

    def compose(self, name, *args, **options):
        return self.command(name, self.base + list(args), **options)

    def python(self, name, script):
        result = self.compose(name, 'exec', '-T', 'backend', 'python', 'manage.py', 'shell',
                              '-c', script)
        marker = next((line[7:] for line in result.stdout.splitlines() if line.startswith('RESULT ')), None)
        return json.loads(marker) if marker else None

    def prepare(self):
        # BuildKit gRPC headers reject non-ASCII context paths on some versions.
        # This ASCII Linux snapshot contains source only, never development data.
        source_root = self.directory / 'source'
        manifest = {}
        denied = {'.venv', '__pycache__', 'node_modules', 'media', 'staticfiles', 'dist',
                  'playwright-report', '.git', '.runtime', 'payment-secrets', 'secrets'}
        for directory in ('backend', 'frontend', 'deploy', 'scripts'):
            for parent, directories, files in os.walk(ROOT / directory):
                directories[:] = sorted(name for name in directories
                    if name not in denied and not name.startswith('test-results'))
                for name in sorted(files):
                    file = Path(parent) / name
                    if (file.is_symlink() or name.startswith('.env')
                            or file.suffix in ('.sqlite3', '.sqlite', '.db', '.pem', '.key', '.p12', '.pfx', '.log', '.pyc')):
                        continue
                    relative = file.relative_to(ROOT)
                    target = source_root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    content = file.read_bytes()
                    target.write_bytes(content)
                    manifest[relative.as_posix()] = hashlib.sha256(content).hexdigest()
        for name in ('.dockerignore', 'compose.yaml', 'compose.payments.yaml'):
            content = (ROOT / name).read_bytes()
            (source_root / name).write_bytes(content)
            manifest[name] = hashlib.sha256(content).hexdigest()
        save_json(self.evidence / 'source-manifest.json', manifest)
        env = dict(os.environ, PUBLIC_DOMAIN='localhost', POSTGRES_PASSWORD=self.secrets[0],
                   DJANGO_SECRET_KEY=self.secrets[1], WECHAT_PAY_ENABLED='false',
                   AMAP_KEY='', AMAP_SECURITY_CODE='')
        # An explicit empty --env-file prevents accidental use of checkout .env.
        raw = self.command('resolve-compose', ['docker', 'compose', '--project-name', self.project,
                              '--env-file', str(self.env_file),
                              '-f', str(ROOT / 'compose.yaml'), 'config', '--format', 'json'],
                             env=env)
        config = json.loads(raw.stdout)
        config.pop('name', None)
        for name, service in config['services'].items():
            service['labels'] = {RUN_LABEL: self.run_id}
            if 'build' in service:
                service['build']['context'] = str(source_root)
                service['build']['labels'] = {RUN_LABEL: self.run_id}
            if name not in ('db', 'web'):
                service['image'] = f'yanhuo-verification-backend:{self.run_id}'
                if name != 'backend':
                    service.pop('build', None)
                service['environment']['DATABASE_URL'] = (
                    f'postgresql://yanhuo:{self.secrets[0]}@db:5432/yanhuo_runtime')
                service['environment']['WECHAT_PAY_CONFIG_FILE'] = ''
                service['environment']['WECHAT_PAY_ENABLED'] = 'false'
                service['environment']['AMAP_KEY'] = ''
                service['environment']['AMAP_SECURITY_CODE'] = ''
            if 'healthcheck' in service:
                service['healthcheck'].update(interval='2s', timeout='10s', retries=60,
                                               start_period='2s')
        config['services']['db']['environment']['POSTGRES_DB'] = 'yanhuo_runtime'
        config['services']['db']['healthcheck']['test'] = [
            'CMD-SHELL', 'pg_isready -U yanhuo -d yanhuo_runtime']
        web = config['services']['web']
        web['image'] = f'yanhuo-verification-web:{self.run_id}'
        # Let Docker reserve a free port; never race a port probe or publish LAN.
        web['ports'] = [{'target': 443, 'published': '0', 'host_ip': '127.0.0.1', 'protocol': 'tcp'}]
        web['networks'] = {'default': None, 'ingress': None}
        for name, volume in config['volumes'].items():
            volume.pop('name', None)
            volume['labels'] = {RUN_LABEL: self.run_id}
        config['networks'] = {'default': {'internal': True, 'labels': {RUN_LABEL: self.run_id}},
                              'ingress': {'labels': {RUN_LABEL: self.run_id}}}
        self.config = config
        save_json(self.config_path, config)
        self.config_path.chmod(0o600)
        git = ['git', '-c', f'safe.directory={ROOT}']
        version = subprocess.run(git + ['rev-parse', 'HEAD'], cwd=ROOT, text=True,
                                 capture_output=True, check=True).stdout.strip()
        status = subprocess.run(git + ['status', '--porcelain'], cwd=ROOT, text=True,
                                capture_output=True, check=True).stdout
        self.report.update(revision=version, dirty_worktree=bool(status),
            source_sha256=hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
            source_manifest='source-manifest.json')
        self.command('docker-version', ['docker', 'version'])
        self.compose('compose-version', 'version')
        try:
            self.compose('build', 'build', 'backend', 'web', timeout=1800)
        finally:
            # A parallel build can produce one image before the other fails.
            # Capture completed, labelled images even on that failure path.
            for image in ('backend', 'web'):
                tag = f'yanhuo-verification-{image}:{self.run_id}'
                inspected = subprocess.run(['docker', 'image', 'inspect', tag], text=True, capture_output=True)
                if inspected.returncode == 0:
                    info = json.loads(inspected.stdout)[0]
                    require(info.get('Config', {}).get('Labels', {}).get(RUN_LABEL) == self.run_id,
                            'Build image ownership mismatch')
                    self.images[tag] = info['Id']
            self.report['images'] = self.images

    def inspect(self, service):
        ids = subprocess.run(self.base + ['ps', '--all', '--quiet', service], text=True,
                             capture_output=True, check=True).stdout.split()
        require(len(ids) == 1, f'Expected one owned {service} container')
        info = json.loads(subprocess.run(['docker', 'inspect', ids[0]], text=True,
                                        capture_output=True, check=True).stdout)[0]
        labels = info['Config'].get('Labels', {})
        require(labels.get(RUN_LABEL) == self.run_id
                and labels.get('com.docker.compose.project') == self.project
                and labels.get('com.docker.compose.project.working_dir') == str(self.directory)
                and labels.get('com.docker.compose.service') == service,
                'Container ownership mismatch')
        return info

    def migration_gate(self):
        broken = self.directory / '9999_verification_failure.py'
        broken.write_text(
            'from django.db import migrations\n'
            'class Migration(migrations.Migration):\n'
            "    dependencies = [('market', '0019_feedback_verification')]\n"
            "    operations = [migrations.RunSQL('SELECT * FROM intentional_missing_verification_table')]\n",
            encoding='utf-8')
        broken.chmod(0o644)
        # Actual migration failure, confined to a read-only test bind mount.
        self.directory.chmod(0o755)
        bad_config = copy.deepcopy(self.config)
        bad_config['services']['release']['volumes'].append({
            'type': 'bind', 'source': str(broken),
            'target': '/app/market/migrations/9999_verification_failure.py', 'read_only': True})
        save_json(self.config_path, bad_config)
        failed = self.compose('migration-failure', 'up', '--detach', '--wait', '--wait-timeout', '150',
                              check=False, timeout=240)
        require(failed.returncode != 0, 'Injected migration unexpectedly succeeded')
        require(self.inspect('release')['State']['ExitCode'] != 0, 'Release did not fail')
        for service in WRITERS:
            require(not self.inspect(service)['State']['Running'], f'{service} started despite failed migration')
        self.report['stages']['migration_failure'] = {'status': 'passed', 'blocked_services': list(WRITERS)}
        self.compose('migration-failure-diagnostics', 'logs', '--no-color', '--tail', '100')
        save_json(self.config_path, self.config)
        # Recreate only the failed release; dependent apps have never started.
        self.compose('remove-failed-release', 'rm', '--force', 'release')
        self.compose('release-success', 'up', '--detach', '--wait', '--wait-timeout', '180', timeout=300)
        self.compose('production-check', 'exec', '-T', 'backend', 'python', 'manage.py',
                     'check', '--deploy', '--fail-level', 'WARNING')
        self.compose('migration-check', 'exec', '-T', 'backend', 'python', 'manage.py', 'migrate', '--check')
        self.compose('model-migration-consistency', 'exec', '-T', 'backend', 'python', 'manage.py',
                     'makemigrations', '--check', '--dry-run')

    def https(self):
        info = self.inspect('web')
        port = info['NetworkSettings']['Ports']['443/tcp'][0]['HostPort']
        origin = f'https://localhost:{port}'
        # A running Caddy container may not have generated its local CA yet.
        # Preserve each failed copy attempt and only trust the actual CA file.
        for attempt in range(30):
            copied = self.compose('test-ca-' + str(attempt), 'cp',
                'web:/data/caddy/pki/authorities/local/root.crt', str(self.directory / 'test-ca.crt'),
                check=False, timeout=15)
            if copied.returncode == 0 and (self.directory / 'test-ca.crt').is_file():
                break
            time.sleep(1)
        else:
            raise RuntimeError('Caddy did not generate its local CA within the verification window')
        context = ssl.create_default_context(cafile=str(self.directory / 'test-ca.crt'))
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(jar), urllib.request.HTTPSHandler(context=context))
        checks = []

        def request(path, data=None, headers=None, expected=200):
            req = urllib.request.Request(origin + path, data=data, headers=headers or {})
            try:
                response = opener.open(req, timeout=15)
            except urllib.error.HTTPError as exc:
                response = exc
            body = response.read()
            require(response.status == expected, f'HTTPS {path}: expected {expected}, got {response.status}')
            checks.append({'path': path, 'status': response.status, 'bytes': len(body)})
            return body, response.headers

        # Wait for Caddy's first TLS certificate without skipping certificate validation.
        for attempt in range(30):
            try:
                body, headers = request('/')
                break
            except (OSError, urllib.error.URLError):
                if attempt == 29:
                    raise
                time.sleep(1)
        require(b'<html' in body.lower(), 'Home is not the SPA')
        require(headers.get('X-Frame-Options') == 'DENY', 'SPA frame protection missing')
        for path in ('/merchant/orders', '/stalls/1', '/orders/00000000-0000-0000-0000-000000000001'):
            deep, _ = request(path)
            require(deep == body, 'Deep-link SPA fallback differs from home')
        assets = re.findall(rb'(?:src|href)="(/assets/[^" ]+)"', body)
        require(bool(assets), 'Built SPA has no assets')
        request(assets[0].decode())
        request('/static/admin/css/base.css')
        request('/api/v1/health')
        request('/api/v1/stalls')
        callback, _ = request('/api/v1/payments/wechat/notify/nonexistent', data=b'{}',
                headers={'Content-Type': 'application/json'}, expected=503)
        require(json.loads(callback).get('code') == 'FAIL', 'Callback rejection did not come from the application')
        fixture = self.python('seed-synthetic', """
import json
from django.conf import settings
from django.db import connection
from market.tests import fixtures
assert connection.settings_dict['NAME'] == 'yanhuo_runtime'
assert not settings.WECHAT_PAY_ENABLED
student, other, vendor, stall, product = fixtures()
print('RESULT ' + json.dumps({'stall_id': stall.pk, 'product_id': product.pk}))
""")
        csrf = json.loads(request('/api/v1/auth/csrf')[0])['csrfToken']
        login, _ = request('/api/v1/auth/login', json.dumps({
            'username': 'merchant', 'password': 'DemoStrong123'}).encode(),
            {'Content-Type': 'application/json', 'X-CSRFToken': csrf, 'Referer': origin + '/'})
        require(json.loads(login).get('username') == 'merchant', 'HTTPS login failed')
        csrf = json.loads(request('/api/v1/auth/csrf')[0])['csrfToken']
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEklEQVR4nGP8MC2AgYGBiQEMABbSAdrelEggAAAAAElFTkSuQmCC')
        boundary = 'YanhuoVerification' + self.run_id
        multipart = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="synthetic.png"\r\n'
                     'Content-Type: image/png\r\n\r\n').encode() + png + f'\r\n--{boundary}--\r\n'.encode()
        uploaded, _ = request(f"/api/v1/merchant/stalls/{fixture['stall_id']}/image", multipart,
            {'Content-Type': f'multipart/form-data; boundary={boundary}',
             'X-CSRFToken': csrf, 'Referer': origin + '/'}, expected=201)
        media_path = json.loads(uploaded)['url']
        media, _ = request(media_path)
        require(media.startswith(b'\xff\xd8'), 'Uploaded media did not round-trip through Caddy')
        require(any(cookie.name == 'sessionid' and cookie.secure for cookie in jar), 'Session cookie is not secure')
        self.report['stages']['https'] = {'status': 'passed', 'origin': origin,
            'trust': 'temporary CA used only by this client; system trust unchanged', 'checks': checks,
            'uploaded_media_sha256': hashlib.sha256(media).hexdigest()}

    def worker_recovery(self):
        # Keep the queued order unexpired until every restarted worker has
        # written a new heartbeat. That prevents a pre-kill expiry from passing.
        self.compose('stop-expiry-worker', 'stop', 'worker')
        pending = self.python('queue-expiry', """
import json
from datetime import timedelta
from django.contrib.auth.models import User
from django.db import connection
from django.utils import timezone
from market.models import Stall, Product, Order
from market.services import create_order
from market.tests import payload
assert connection.settings_dict['NAME'] == 'yanhuo_runtime'
stall = Stall.objects.get(name='测试烤冷面')
product = Product.objects.get(stall=stall)
stock_before = product.stock
order, created = create_order(User.objects.get(username='tester'), payload(stall, product))
assert created
Order.objects.filter(pk=order.pk).update(expires_at=timezone.now()+timedelta(minutes=15))
product.refresh_from_db()
assert product.stock == stock_before - 1
print('RESULT ' + json.dumps({'order_id': str(order.pk), 'product_id': product.pk,
                              'stock_before': stock_before, 'stock_reserved': product.stock}))
""")
        self.compose('start-expiry-worker', 'start', 'worker')
        recovered = []
        for service, heartbeat in {'worker': 'expire_orders', 'payment_worker': 'reconcile_payments',
                                   'notification_worker': 'process_payment_notifications'}.items():
            old = self.inspect(service)
            # Docker's kill API marks a manual stop and suppresses restart. Send
            # SIGINT from an exec process instead: Python's PID 1 handler exits
            # with KeyboardInterrupt, and the unmodified restart policy applies.
            self.command('kill-' + service, ['docker', 'exec', old['Id'], 'python', '-c',
                'import os,signal;os.kill(1,signal.SIGINT)'])
            kill_completed_at = time.time()
            for attempt in range(90):
                current = self.inspect(service)
                if current['State']['Running'] and current['RestartCount'] > old['RestartCount']:
                    break
                time.sleep(1)
            else:
                raise RuntimeError(f'{service} did not automatically restart')
            recovered.append({'service': service, 'container_id': old['Id'],
                              'restart_count': current['RestartCount'], 'heartbeat_name': heartbeat,
                              'termination': 'SIGINT to Python PID 1 via owned-container exec',
                              'kill_completed_at': kill_completed_at,
                              'restarted_at': current['State']['StartedAt']})
        for attempt in range(45):
            state = self.python('worker-heartbeat-' + str(attempt), f"""
import json
from market.models import Order, Product
from market.operational_models import WorkerHeartbeat
from market.runtime_health import operations_status
order = Order.objects.get(pk={pending['order_id']!r})
print('RESULT ' + json.dumps({{'order_status': order.status, 'released': order.inventory_released,
                              'stock': Product.objects.get(pk={pending['product_id']!r}).stock,
                              'heartbeats': {{row.name: {{'last_success_at': row.last_success_at.isoformat() if row.last_success_at else None,
                                  'last_success_epoch': row.last_success_at.timestamp() if row.last_success_at else 0}}
                                  for row in WorkerHeartbeat.objects.all()}},
                              'operations': operations_status()}}))
""")
            require(state['order_status'] == 'pending' and not state['released']
                    and state['stock'] == pending['stock_reserved'],
                    'Queued order changed before restart verification completed')
            fresh = all(state['heartbeats'].get(item['heartbeat_name'], {}).get('last_success_epoch', 0)
                        > item['kill_completed_at'] for item in recovered)
            if fresh and state['operations']['status'] == 'ok':
                break
            time.sleep(2)
        else:
            raise RuntimeError('Recovered workers did not write post-kill successful heartbeats')
        heartbeat_evidence = state['heartbeats']
        armed = self.python('arm-expiry-after-restart', f"""
import json
from datetime import timedelta
from django.utils import timezone
from market.models import Order, Product
order = Order.objects.get(pk={pending['order_id']!r})
assert order.status == 'pending' and not order.inventory_released
assert Product.objects.get(pk={pending['product_id']!r}).stock == {pending['stock_reserved']!r}
expires_at = timezone.now() + timedelta(seconds=2)
Order.objects.filter(pk=order.pk).update(expires_at=expires_at)
print('RESULT ' + json.dumps({{'expires_at': expires_at.isoformat(), 'queued_status': order.status}}))
""")
        for attempt in range(45):
            state = self.python('worker-expiry-' + str(attempt), f"""
import json
from market.models import Order, Product, AuditLog
from market.runtime_health import operations_status
order = Order.objects.get(pk={pending['order_id']!r})
print('RESULT ' + json.dumps({{'order_status': order.status, 'released': order.inventory_released,
    'stock': Product.objects.get(pk={pending['product_id']!r}).stock,
    'expiry_audit_records': AuditLog.objects.filter(action='order_expired', target=str(order.pk)).count(),
    'operations': operations_status()}}))
""")
            if state['order_status'] == 'cancelled' and state['released'] and state['operations']['status'] == 'ok':
                break
            time.sleep(2)
        else:
            raise RuntimeError('Restarted expiry worker did not finish its queued order')
        require(state['stock'] == pending['stock_reserved'] + 1 == pending['stock_before']
                and state['expiry_audit_records'] == 1, 'Expiry did not restore exactly one reserved portion')
        self.compose('repeat-expiry-after-recovery', 'exec', '-T', 'backend', 'python', 'manage.py', 'expire_orders')
        repeated = self.python('verify-repeat-expiry', f"""
import json
from market.models import Order, Product, AuditLog
order = Order.objects.get(pk={pending['order_id']!r})
print('RESULT ' + json.dumps({{'order_status': order.status, 'released': order.inventory_released,
    'stock': Product.objects.get(pk={pending['product_id']!r}).stock,
    'expiry_audit_records': AuditLog.objects.filter(action='order_expired', target=str(order.pk)).count()}}))
""")
        require(repeated['order_status'] == 'cancelled' and repeated['released']
                and repeated['stock'] == state['stock'] and repeated['expiry_audit_records'] == 1,
                'Repeated expiry changed already-released inventory or duplicated the expiry event')
        self.report['stages']['worker_recovery'] = {'status': 'passed', 'workers': recovered,
            'post_kill_heartbeats': heartbeat_evidence, 'expiry_armed_after_restarts': armed,
            'inventory': {'before_reservation': pending['stock_before'], 'reserved': pending['stock_reserved'],
                          'after_recovery': state['stock'], 'after_repeat_expiry': repeated['stock'],
                          'expiry_audit_records': repeated['expiry_audit_records']},
            'queued_order_released_once': True, 'operations': state['operations']}

    def acceptance(self, full_regression=False):
        label = 'market' if full_regression else 'market.test_runtime_acceptance'
        result = self.compose('postgres-regression' if full_regression else 'runtime-acceptance',
            'exec', '-T', '-e', 'DJANGO_ENV=development', '-e', 'DEMO_MODE=true',
            '-e', 'RUNTIME_ACCEPTANCE_EVIDENCE_DIR=/tmp/runtime-notification-evidence',
            '-e', 'RUNTIME_VERSION=' + self.report['revision'] + '+' + self.report['source_sha256'],
            'backend', 'python', 'manage.py', 'test', label, '--noinput', '--verbosity', '2', timeout=1800,
            check=False)
        self.compose('notification-process-logs', 'cp', 'backend:/tmp/runtime-notification-evidence',
                     str(self.evidence / 'notification-processes'), check=False)
        reports = []
        for line in (result.stdout + '\n' + result.stderr).splitlines():
            match = re.search(r'(RUNTIME_\w+_RESULTS|DISCOVERY_SCALE_RESULTS)\s*[:=]?\s*(\{.*|\[.*)', line)
            if match:
                reports.append({'name': match[1], 'result': json.loads(match[2])})
        self.report['stages']['postgres'] = {'status': 'passed' if not result.returncode else 'failed',
                                           'test_label': label, 'reports': reports}
        require(not result.returncode, 'PostgreSQL acceptance failed; see test log and per-cohort results')

    def backup(self):
        self.report['host_tools']['backup'] = {name: hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest()
            for name in ('verify_runtime_backup.py', 'backup.py')}
        self.command('backup-runtime', [sys.executable, ROOT / 'scripts/verify_runtime_backup.py',
            '--project-dir', self.directory, '--project-name', self.project,
            '--env-file', self.env_file, '--evidence-dir', self.evidence / 'backup'], timeout=1800)
        self.report['stages']['backup'] = {'status': 'passed', 'evidence': 'backup/',
                                         'scope': 'same-host isolated SFTP; not offsite acceptance'}

    def monitor(self):
        import operations_monitor as monitor
        self.report['host_tools']['monitor'] = {name: hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest()
            for name in ('operations_monitor.py', 'verify_operations_monitor.py')}
        with tempfile.TemporaryDirectory(prefix='yanhuo-monitor-') as directory:
            private = Path(directory)
            for name, value in (('runtime.env', ''), ('webhook.txt', 'https://127.0.0.1:1/unused')):
                (private / name).write_text(value, encoding='utf-8')
                (private / name).chmod(0o600)
            path = private / 'operations.json'
            save_json(path, {'project_dir': str(self.directory), 'project_name': self.project,
                'env_file': str(private / 'runtime.env'), 'state_dir': str(private / 'state'),
                'backup_state_dir': str(private / 'backup'), 'webhook_file': str(private / 'webhook.txt')})
            path.chmod(0o600)
            config = monitor.Config(path)
            for attempt in range(30):
                baseline = monitor.collect(config, time.time())
                if baseline == {'backup_unavailable': 1}:
                    break
                time.sleep(2)
            require(baseline == {'backup_unavailable': 1}, f'Unexpected host monitor baseline: {baseline}')
            self.python('monitor-business-fixture', """
import hashlib
from market.models import PaymentNotification, Order
PaymentNotification.objects.create(account_key='verification', event_id='verification-dead',
    event_type='TRANSACTION.SUCCESS', resource={}, payload_hash=hashlib.sha256(b'fixture').hexdigest(),
    mchid='synthetic', appid='synthetic', status='dead', conflict_count=1)
Order.objects.filter(idempotency_key__isnull=False).update(payment_review_required=True)
""")
            issues = monitor.collect(config, time.time())
            require(issues.get('notifications_dead') == 1 and issues.get('notifications_conflicts') == 1
                    and issues.get('payments_review') == 1, f'Monitor missed business anomalies: {issues}')
            require(not any(key.startswith('worker_') or key.startswith('containers_') for key in issues),
                    'Business failure fixture unexpectedly stopped a worker')
            self.python('remove-monitor-fixture', """
from market.models import PaymentNotification, Order
PaymentNotification.objects.filter(account_key='verification', event_id='verification-dead').delete()
Order.objects.update(payment_review_required=False)
""")
            recovery = monitor.collect(config, time.time())
            require(recovery == baseline, 'Host monitor did not observe recovery')
            self.command('monitor-https', [sys.executable, ROOT / 'scripts/verify_operations_monitor.py',
                '--report', self.evidence / 'monitor-https.json'], timeout=180)
            self.report['stages']['monitor'] = {'status': 'passed', 'baseline': baseline,
                'business_failure_with_workers_alive': issues, 'recovery': recovery,
                'delivery_evidence': 'monitor-https.json'}

    def diagnostics(self):
        if self.config_path.exists():
            self.compose('final-services', 'ps', '--all', check=False, timeout=30)
            self.compose('final-service-logs', 'logs', '--no-color', '--tail', '200', check=False, timeout=30)

    def cleanup(self):
        # Enumerate by unique label, validate Compose ownership, then remove by
        # immutable IDs (volumes by Docker's unique name). Never down a directory.
        removed = []
        for kind, list_args, inspect_args, delete_args in (
                ('container', ['ps', '-aq'], ['inspect'], ['rm', '--force']),
                ('volume', ['volume', 'ls', '-q'], ['volume', 'inspect'], ['volume', 'rm']),
                ('network', ['network', 'ls', '-q'], ['network', 'inspect'], ['network', 'rm'])):
            result = subprocess.run(['docker'] + list_args + ['--filter', f'label={RUN_LABEL}={self.run_id}'],
                                    text=True, capture_output=True, check=True)
            for resource in result.stdout.split():
                info = json.loads(subprocess.run(['docker'] + inspect_args + [resource],
                    text=True, capture_output=True, check=True).stdout)[0]
                labels = info.get('Config', {}).get('Labels', {}) if kind == 'container' else info.get('Labels', {})
                require(labels.get(RUN_LABEL) == self.run_id
                        and labels.get('com.docker.compose.project') == self.project, 'Cleanup ownership mismatch')
                if kind == 'container':
                    require(labels.get('com.docker.compose.project.working_dir') == str(self.directory),
                            'Cleanup directory mismatch')
                self.command('cleanup-' + kind + '-' + resource[-20:], ['docker'] + delete_args + [resource], timeout=120)
                removed.append({'kind': kind, 'id': resource})
        for tag, identifier in self.images.items():
            actual = subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag],
                                    text=True, capture_output=True, check=True).stdout.strip()
            require(actual == identifier, 'Image tag ownership changed')
            self.command('cleanup-image-' + tag.split(':')[0], ['docker', 'image', 'rm', tag])
        self.report['cleanup'] = {'status': 'passed', 'resources': removed}
        shutil.rmtree(self.directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence-dir', type=Path)
    parser.add_argument('--stages', choices=('deployment', 'acceptance', 'all'), default='all')
    parser.add_argument('--full-regression', action='store_true')
    args = parser.parse_args()
    require(os.name == 'posix', 'Use scripts/verify-runtime.ps1 from Windows or run this entry in Linux/WSL')
    for binary in ('docker', 'git') + (('restic', 'ssh', 'ssh-keygen', 'openssl') if args.stages == 'all' else ()):
        require(shutil.which(binary), f'Required executable missing: {binary}')
    evidence = args.evidence_dir or ROOT / '.runtime' / ('runtime-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
    run = Verification(evidence)
    def interrupted(signum, frame):
        raise RuntimeError('Verification interrupted; collecting diagnostics and cleaning owned resources')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    print(f'Evidence: {run.evidence}', flush=True)
    success = False
    try:
        run.prepare()
        run.migration_gate()
        run.https()
        run.worker_recovery()
        run.monitor()
        if args.stages != 'deployment':
            run.acceptance(args.full_regression)
        if args.stages == 'all':
            run.backup()
        success = True
    except Exception as exc:
        run.report['failure'] = {'type': type(exc).__name__, 'message': str(exc)}
        print(f'FAILED: {exc}', file=sys.stderr, flush=True)
    finally:
        try:
            run.diagnostics()
        except Exception as exc:
            run.report['diagnostics_error'] = type(exc).__name__
        try:
            run.cleanup()
        except Exception as exc:
            success = False
            run.report['cleanup'] = {'status': 'failed', 'error': str(exc), 'private_directory': str(run.directory)}
        run.report.update(status='passed' if success else 'failed', finished_at=time.time())
        save_json(run.evidence / 'report.json', run.report)
    print(f'{run.report["status"].upper()}: {run.evidence / "report.json"}', flush=True)
    return 0 if success else 1


if __name__ == '__main__':
    raise SystemExit(main())
