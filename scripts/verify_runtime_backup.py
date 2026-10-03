"""Exercise the production backup path against an explicitly marked disposable stack.

Linux/WSL/CI only. Requires docker, restic, openssl and OpenSSH client tools.
This is a SAME-HOST SFTP simulation, never evidence of independent offsite storage.
The Compose stack must have been created by verify_runtime.py (including its
marker and per-container run label). No development or production stack is allowed.
"""
from argparse import ArgumentParser
from contextlib import contextmanager
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

SPEC = importlib.util.spec_from_file_location('production_backup', Path(__file__).with_name('backup.py'))
backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup)
MONITOR_SPEC = importlib.util.spec_from_file_location('production_monitor', Path(__file__).with_name('operations_monitor.py'))
monitor = importlib.util.module_from_spec(MONITOR_SPEC)
MONITOR_SPEC.loader.exec_module(monitor)
LABEL = 'yanhuo.backup-verification.run'
MARKER = '.yanhuo-runtime-verification.json'


class VerificationError(RuntimeError):
    """Only fixed, non-sensitive diagnostic codes may be used."""


def command(args, *, env=None, timeout=600):
    result = subprocess.run([str(arg) for arg in args], env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode:
        raise VerificationError('verification_command_failed_' + Path(str(args[0])).name)
    return result.stdout.decode().strip()


def private_write(path, content):
    path.write_text(content, encoding='utf-8')
    path.chmod(0o600)
    return path


def isolation_marker(project, name):
    project = Path(project).resolve(strict=True)
    marker_path = project / MARKER
    if marker_path.is_symlink() or not marker_path.is_file():
        raise VerificationError('isolated_stack_marker_required')
    marker = json.loads(marker_path.read_text(encoding='utf-8'))
    if (marker.get('synthetic_only') is not True or marker.get('project_name') != name
            or not isinstance(marker.get('run_id'), str) or not marker['run_id']
            or not name.startswith('yanhuo-verify-')):
        raise VerificationError('isolated_stack_marker_mismatch')
    return marker


def assert_stack_isolation(config, marker):
    running = backup.containers(config, ('db', *backup.WRITERS))
    if set(running) != {'db', *backup.WRITERS}:
        raise VerificationError('all_disposable_writers_must_be_running')
    for identifier in running.values():
        labels = json.loads(command(['docker', 'inspect', '--format', '{{json .Config.Labels}}', identifier]))
        if labels.get('yanhuo.verification.run') != marker['run_id']:
            raise VerificationError('disposable_container_run_label_mismatch')
    return running


def assert_writers_resumed(config, original):
    current = backup.containers(config, backup.WRITERS)
    if current != {key: value for key, value in original.items() if key in backup.WRITERS}:
        raise VerificationError('original_writers_not_resumed')
    if config.maintenance.exists():
        raise VerificationError('maintenance_checkpoint_not_cleared')


WORKER_NAMES = {'worker': 'expire_orders', 'payment_worker': 'reconcile_payments',
                'notification_worker': 'process_payment_notifications'}


def recovery_is_complete(states, probe):
    """A stale successful heartbeat is not proof that a resumed worker works."""
    if set(states) != set(backup.WRITERS) or probe.get('api') != {'status': 'ok', 'http_status': 200}:
        return False
    for service, state in states.items():
        if not state.get('running') or state.get('health') != 'healthy':
            return False
        if service in WORKER_NAMES:
            started = datetime.fromisoformat(state['started_at'].replace('Z', '+00:00')).timestamp()
            success = probe.get('heartbeats', {}).get(WORKER_NAMES[service], {}).get('last_success_epoch')
            if not isinstance(success, (int, float)) or success <= started:
                return False
    return True


def assert_writers_recovered(config, original, evidence, *, timeout=150):
    """Wait for actual API health and work completed after each last restart."""
    assert_writers_resumed(config, original)
    identifiers = {identifier: service for service, identifier in original.items() if service in backup.WRITERS}
    attempts = []
    deadline = time.monotonic() + timeout
    script = '''import json, urllib.request
from market.operational_models import WorkerHeartbeat
request = urllib.request.Request('http://127.0.0.1:8000/api/v1/health',
    headers={'Host': 'localhost', 'X-Forwarded-Proto': 'https'})
with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=5) as response:
    api = json.loads(response.read())
    api['http_status'] = response.status
heartbeats = {row.name: {'last_success_at': row.last_success_at.isoformat() if row.last_success_at else None,
    'last_success_epoch': row.last_success_at.timestamp() if row.last_success_at else None}
    for row in WorkerHeartbeat.objects.all()}
print('RECOVERY ' + json.dumps({'api': api, 'heartbeats': heartbeats}))
'''
    try:
        while time.monotonic() < deadline:
            rows = json.loads(command(['docker', 'inspect', *identifiers]))
            states = {}
            for row in rows:
                service = identifiers.get(row['Id'])
                labels = row['Config'].get('Labels', {})
                if (not service or labels.get('com.docker.compose.project') != config.name
                        or labels.get('com.docker.compose.project.working_dir') != str(config.project)
                        or labels.get('com.docker.compose.service') != service):
                    raise VerificationError('writer_recovery_ownership_mismatch')
                states[service] = {'container_id': row['Id'], 'running': row['State']['Running'],
                                   'health': row['State'].get('Health', {}).get('Status'),
                                   'started_at': row['State']['StartedAt']}
            entry = {'observed_at': backup.utcnow(), 'containers': states}
            try:
                probe = subprocess.run(['docker', 'exec', original['backend'], 'python', 'manage.py', 'shell', '-c', script],
                                       capture_output=True, text=True, timeout=30)
            except subprocess.TimeoutExpired as error:
                def decoded(value):
                    return value.decode(errors='replace') if isinstance(value, bytes) else value or ''
                (evidence / ('writer-recovery-' + str(len(attempts)) + '.log')).write_text(
                    decoded(error.stdout) + '\n' + decoded(error.stderr), encoding='utf-8')
                entry['probe_error'] = 'timed_out'
                attempts.append(entry)
                continue
            (evidence / ('writer-recovery-' + str(len(attempts)) + '.log')).write_text(
                probe.stdout + '\n' + probe.stderr, encoding='utf-8')
            if probe.returncode == 0:
                payloads = [line[9:] for line in probe.stdout.splitlines() if line.startswith('RECOVERY ')]
                if len(payloads) == 1:
                    entry['probe'] = json.loads(payloads[0])
            attempts.append(entry)
            if recovery_is_complete(states, entry.get('probe', {})):
                operation = subprocess.run(['docker', 'exec', original['backend'], 'python', 'manage.py',
                                            'check_operations'], capture_output=True, text=True, timeout=30)
                (evidence / 'writer-recovery-check-operations.log').write_text(
                    operation.stdout + '\n' + operation.stderr, encoding='utf-8')
                if operation.returncode != 0:
                    raise VerificationError('operations_unhealthy_after_backup_faults')
                entry['operations'] = json.loads(operation.stdout)
                return entry
            time.sleep(2)
        raise VerificationError('writers_did_not_recover_health_and_fresh_heartbeats')
    finally:
        backup.write_json(evidence / 'writer-recovery-attempts.json', attempts)


def remove_owned(kind, identifier, run_id):
    if kind == 'container':
        raw = command(['docker', 'inspect', '--format', '{{json .Config.Labels}}', identifier])
    elif kind == 'volume':
        raw = command(['docker', 'volume', 'inspect', '--format', '{{json .Labels}}', identifier])
    else:
        raise VerificationError('unsupported_cleanup_resource')
    if json.loads(raw).get(LABEL) != run_id:
        raise VerificationError('auxiliary_resource_ownership_mismatch')
    command(['docker', 'rm', '--force', identifier] if kind == 'container'
            else ['docker', 'volume', 'rm', identifier])


class Receiver:
    def __init__(self, directory):
        self.events = []
        self.fail = False
        cert, key = directory / 'receiver.crt', directory / 'receiver.key'
        command(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                 '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1',
                 '-keyout', key, '-out', cert])
        key.chmod(0o600)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 4096:
                    self.send_error(413)
                    return
                owner.events.append(json.loads(self.rfile.read(length)))
                self.send_response(503 if owner.fail else 204)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.certificate = cert
        self.url = 'https://127.0.0.1:' + str(self.server.server_port) + '/backup-alert'

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


SFTP_DOCKERFILE = '''FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends openssh-server \\
    && rm -rf /var/lib/apt/lists/* && mkdir -p /run/sshd /etc/ssh/authorized_keys \\
    && useradd --uid 10001 --home-dir /home/backup --shell /usr/sbin/nologin yanhuo_backup \\
    && usermod -p '*' yanhuo_backup && mkdir -p /home/backup/repository \\
    && chown root:root /home/backup && chmod 755 /home/backup \\
    && chown yanhuo_backup:yanhuo_backup /home/backup/repository
CMD ["/usr/sbin/sshd", "-D", "-e", "-f", "/fixture/sshd_config"]
'''


@contextmanager
def sftp_fixture(directory, run_id, evidence, cleanup=None):
    """Ephemeral public-key-only SFTP; host key pinned, password auth disabled."""
    image = 'yanhuo-verification-sftp:1'
    build = directory / 'sftp-build'
    build.mkdir()
    (build / 'Dockerfile').write_text(SFTP_DOCKERFILE, encoding='utf-8')
    built = subprocess.run(['docker', 'build', '--tag', image, str(build)],
                           capture_output=True, timeout=900)
    (evidence / 'sftp-image-build.log').write_bytes(built.stdout + built.stderr)
    if built.returncode:
        raise VerificationError('sftp_image_build_failed')
    files = directory / 'sftp-config'
    files.mkdir(mode=0o700)
    for filename in ('client', 'host'):
        command(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', files / filename])
    private_write(files / 'sshd_config', '''Port 22
ListenAddress 0.0.0.0
HostKey /fixture/host
PidFile /run/sshd.pid
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
UsePAM no
AuthorizedKeysFile /fixture/client.pub
StrictModes yes
AllowUsers yanhuo_backup
Subsystem sftp internal-sftp
Match User yanhuo_backup
    ChrootDirectory /home/backup
    ForceCommand internal-sftp
    AllowTcpForwarding no
    X11Forwarding no
''')
    # OpenSSH reads authorized_keys after changing to the target user. Only the
    # public key needs to be readable; private host/client keys remain 0600.
    files.chmod(0o755)
    (files / 'client.pub').chmod(0o644)
    volume = 'yanhuo-backup-sftp-' + run_id
    identifier = None
    command(['docker', 'volume', 'create', '--label', LABEL + '=' + run_id, volume])
    try:
        identifier = command(['docker', 'create', '--label', LABEL + '=' + run_id,
                              '--publish', '127.0.0.1::22', '--mount',
                              f'type=bind,source={files},target=/fixture,readonly', '--mount',
                              f'type=volume,source={volume},target=/home/backup/repository', image])
        command(['docker', 'start', identifier])
        inspect = json.loads(command(['docker', 'inspect', identifier]))[0]
        binding = inspect['NetworkSettings']['Ports']['22/tcp'][0]
        if binding['HostIp'] != '127.0.0.1':
            raise VerificationError('sftp_must_bind_loopback_only')
        port = binding['HostPort']
        public = (files / 'host.pub').read_text().split()
        known_hosts = private_write(directory / 'known_hosts', f'[127.0.0.1]:{port} {public[0]} {public[1]}\n')
        ssh = [shutil.which('ssh'), '-F', '/dev/null', '-i', str(files / 'client'), '-p', port,
               '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
               '-o', 'ConnectTimeout=5', '-o', 'UserKnownHostsFile=' + str(known_hosts),
               'yanhuo_backup@127.0.0.1', '-s', 'sftp']
        yield identifier, ssh
    finally:
        # Save diagnostics before deleting only IDs whose random run label agrees.
        if identifier:
            result = subprocess.run(['docker', 'logs', identifier], capture_output=True)
            (evidence / 'sftp.log').write_bytes(result.stdout + result.stderr)
            remove_owned('container', identifier, run_id)
        remove_owned('volume', volume, run_id)
        if cleanup is not None:
            cleanup['auxiliary_resources_removed'] = True


def tool_wrappers(directory, ssh, database_id):
    """Test-only subprocess fault injection; production has no fault switches."""
    bin_dir = directory / 'bin'
    bin_dir.mkdir(mode=0o700)
    executables = {name: shutil.which(name) for name in ('restic', 'docker')}
    restic_script = ('#!' + sys.executable + '\nimport os, sys\n'
                    + 'os.execv(' + repr(executables['restic']) + ', '
                    + repr([executables['restic'], '-o', 'sftp.command=' + shlex.join(ssh)]) + ' + sys.argv[1:])\n')
    docker_script = ('#!' + sys.executable + '\nimport os, sys\n'
                     + 'if os.environ.get("YANHUO_TEST_FAIL_DUMP") == "1" and sys.argv[1:4] == '
                     + repr(['exec', database_id, 'pg_dump']) + ':\n    sys.exit(73)\n'
                     + 'os.execv(' + repr(executables['docker']) + ', ' + repr([executables['docker']])
                     + ' + sys.argv[1:])\n')
    for name, script in [('restic', restic_script), ('docker', docker_script)]:
        target = private_write(bin_dir / name, script)
        target.chmod(0o700)
    return bin_dir


def run_backup_cli(config, operation, evidence, label, *, expect_success=True, extra_env=None,
                   snapshot=None, corrupt_restore=False):
    entry = str(Path(__file__).with_name('backup.py'))
    args = [sys.executable, entry, operation]
    if snapshot:
        args += ['--snapshot', snapshot]
    if corrupt_restore:
        if operation != 'restore-check':
            raise VerificationError('corrupt_restore_fault_requires_restore_check')
        # This temporary child interpreter is the only fault hook. Production
        # main() owns failure persistence, alert delivery and resource cleanup;
        # the actual pg_restore consumes corrupted bytes and must fail itself.
        program = '''import importlib.util, sys
spec = importlib.util.spec_from_file_location('backup_under_test', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
original = module.restore_bundle
def corrupt_after_download(*args, **kwargs):
    bundle, manifest = original(*args, **kwargs)
    (bundle / 'database.dump').write_bytes(b'damaged dump fixture')
    print('test_fault=corrupted_downloaded_dump', flush=True)
    return bundle, manifest
module.restore_bundle = corrupt_after_download
sys.argv = sys.argv[1:]
sys.exit(module.main())
'''
        args = [sys.executable, '-c', program, *args[1:]]
    result = subprocess.run(args,
                            env={**config.env, **(extra_env or {})}, capture_output=True, timeout=1800)
    (evidence / (label + '.log')).write_bytes(result.stdout + result.stderr)
    if (result.returncode == 0) != expect_success:
        raise VerificationError('unexpected_backup_result_' + label)
    return result


def record_restore_monitor(config, evidence, label, *, restore_failed):
    """Read actual durable state; preserve it before evaluating the assertion."""
    issues = monitor.backup_issues(SimpleNamespace(backup=config.state, backup_age=config.rpo), time.time())
    record = {'observed_at': backup.utcnow(), 'issues': issues, 'expected_restore_failed': restore_failed}
    for filename in ('last-success.json', 'last-failure.json', 'last-restore-failure.json', 'last-restore.json'):
        path = config.state / filename
        record[filename] = json.loads(path.read_text()) if path.exists() else None
    backup.write_json(evidence / (label + '.json'), record)
    if bool(issues.get('restore_failed')) != restore_failed:
        raise VerificationError('incorrect_restore_monitor_result_' + label)
    return record


def retain_fixture_snapshot(config, snapshot, run_id):
    """Keep the known-good fixture through same-hour production retention.

    Adding a tag changes the snapshot ID, but not its data tree. Production
    retention groups by host and tags, so the unique fixture group stays owned
    by this disposable repository without weakening the production policy.
    """
    anchor = 'verification-restore-anchor-' + run_id
    before = json.loads(backup.restic(config, 'snapshots', '--json', snapshot))
    if len(before) != 1 or before[0]['id'] != snapshot:
        raise VerificationError('original_restore_snapshot_not_unique')
    backup.restic(config, 'tag', '--add', anchor, snapshot)
    after = json.loads(backup.restic(config, 'snapshots', '--json', '--host', config.name,
                                    '--tag', config.tag + ',' + anchor))
    if len(after) != 1 or after[0]['tree'] != before[0]['tree'] or after[0]['time'] != before[0]['time']:
        raise VerificationError('retained_restore_snapshot_content_changed')
    return {'original_snapshot_id': snapshot, 'retained_snapshot_id': after[0]['id'],
            'tree': after[0]['tree'], 'fixture_retention_tag': anchor}


def restore_resources():
    return {
        'containers': sorted(command(['docker', 'ps', '--all', '--quiet', '--filter', 'label=yanhuo.restore-run']).split()),
        'volumes': sorted(command(['docker', 'volume', 'ls', '--quiet', '--filter', 'label=yanhuo.restore-run']).split()),
    }


def run_verification(project, name, env_file, evidence):
    if os.name == 'nt':
        raise VerificationError('run_in_linux_wsl_or_ci')
    for tool in ('docker', 'restic', 'openssl', 'ssh', 'ssh-keygen'):
        if not shutil.which(tool):
            raise VerificationError('missing_tool_' + tool)
    project = Path(project).resolve(strict=True)
    marker = isolation_marker(project, name)
    env_file = backup.private_file(Path(env_file).resolve(strict=True))
    evidence = Path(evidence).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    report = {'schema_version': 1, 'suite': 'production-backup-runtime', 'status': 'running',
              'started_at': backup.utcnow(), 'project_name': name, 'run_id': marker['run_id'],
              'scope': 'same_host_isolated_sftp_not_offsite_acceptance', 'checks': [],
              'database': 'PostgreSQL 17 / PostGIS 3.5', 'real_payment_credentials': False,
              'rpo_target_seconds': 3600, 'rto_target_seconds': 7200}
    report['tool_versions'] = {
        'restic': command(['restic', 'version']),
        'docker': command(['docker', 'version', '--format', '{{.Server.Version}}']),
        'python': sys.version.split()[0],
    }
    source_root = str(Path(__file__).resolve().parents[1])
    version = subprocess.run(['git', '-c', 'safe.directory=' + source_root, '-C', source_root, 'rev-parse', 'HEAD'],
                             capture_output=True)
    report['code_revision'] = version.stdout.decode().strip() if version.returncode == 0 else 'unavailable'
    report['working_tree_included'] = True
    started = time.monotonic()
    cleanup = {'receiver_stopped': False, 'auxiliary_resources_removed': False}
    overlay_created = False
    overlay = project / 'compose.payments.yaml'
    media_path = None
    config = None
    original = None
    try:
        with tempfile.TemporaryDirectory(prefix='yanhuo-backup-verification-') as temporary:
            private = Path(temporary)
            private.chmod(0o700)
            receiver = Receiver(private)
            try:
                private_write(private / 'webhook', receiver.url)
                private_write(private / 'password', secrets.token_urlsafe(48))
                payments = private / 'payment-secrets'
                payments.mkdir(mode=0o700)
                private_write(payments / 'wechat-accounts.json', json.dumps({
                    'verification_only': True, 'accounts': [],
                    'description': 'synthetic recovery fixture; never supplied to payment services'}))
                command(['openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048',
                         '-out', payments / 'synthetic-merchant-private.pem'])
                (payments / 'synthetic-merchant-private.pem').chmod(0o600)
                if not overlay.exists():
                    private_write(overlay, '{"services": {}}\n')
                    overlay_created = True
                # Never inherit another repository selector/password/provider
                # credential from the operator's real backup environment.
                ambient = {key: value for key, value in os.environ.items()
                           if not key.startswith(('BACKUP_', 'RESTIC_', 'WECHAT_PAY_', 'AWS_', 'AZURE_'))}
                env = {**ambient, 'BACKUP_PROJECT_DIR': str(project), 'BACKUP_PROJECT_NAME': name,
                       'BACKUP_ENV_FILE': str(env_file), 'BACKUP_STATE_DIR': str(private / 'state'),
                       'BACKUP_MAINTENANCE_APPROVED': 'true', 'BACKUP_OFFSITE_CONFIRMED': 'true',
                       'BACKUP_PAYMENTS_CONFIGURED': 'true', 'WECHAT_PAY_SECRETS_DIR': str(payments),
                       'RESTIC_REPOSITORY': 'sftp:yanhuo_backup@127.0.0.1:/repository',
                       'RESTIC_PASSWORD_FILE': str(private / 'password'),
                       'BACKUP_FAILURE_WEBHOOK_FILE': str(private / 'webhook'),
                       'SSL_CERT_FILE': str(receiver.certificate),
                       'NO_PROXY': '127.0.0.1,localhost', 'no_proxy': '127.0.0.1,localhost'}
                config = backup.Config(env)
                original = assert_stack_isolation(config, marker)
                report['checks'].append({'name': 'stack_ownership_and_isolation', 'status': 'passed'})
                fixture_code = ('import json;from tools.runtime_backup_fixture import seed_runtime_backup_fixture;'
                                'print("BACKUP_FIXTURE " + json.dumps(seed_runtime_backup_fixture('
                                + repr(marker['run_id']) + ')))')
                fixture_output = command(['docker', 'exec', original['backend'], 'python', 'manage.py',
                                          'shell', '-c', fixture_code])
                fixture_lines = [line[15:] for line in fixture_output.splitlines() if line.startswith('BACKUP_FIXTURE ')]
                if len(fixture_lines) != 1:
                    raise VerificationError('synthetic_financial_fixture_not_confirmed')
                financial_fixture = json.loads(fixture_lines[0])
                report['financial_fixture'] = financial_fixture
                media_path = '/app/media/runtime-verification/backup-' + secrets.token_hex(8) + '.png'
                png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aN1kAAAAASUVORK5CYII='
                command(['docker', 'exec', original['backend'], 'python', '-c',
                         'import base64,pathlib;p=pathlib.Path(' + repr(media_path) + ');'
                         'p.parent.mkdir(exist_ok=True);p.write_bytes(base64.b64decode(' + repr(png) + '))'])
                with sftp_fixture(private, secrets.token_hex(8), evidence, cleanup) as (_sftp, ssh):
                    wrappers = tool_wrappers(private, ssh, original['db'])
                    config.env['PATH'] = str(wrappers) + os.pathsep + os.environ['PATH']
                    for attempt in range(20):
                        try:
                            backup.restic(config, 'init', timeout=30)
                            break
                        except backup.BackupError:
                            if attempt == 19:
                                raise VerificationError('sftp_repository_not_ready')
                            time.sleep(0.5)
                    report['transport'] = {'type': 'SFTP', 'host_key_verification': 'pinned_ed25519',
                                           'binding': '127.0.0.1', 'encryption': 'restic_password',
                                           'alert_tls': 'temporary_CA_client_trust_only'}
                    run_backup_cli(config, 'backup', evidence, 'successful-backup')
                    assert_writers_resumed(config, original)
                    success = json.loads(config.success.read_text())
                    report['checks'].append({'name': 'production_capture_encrypt_upload_download',
                                              'status': 'passed', **success})
                    before_restore = restore_resources()
                    run_backup_cli(config, 'restore-check', evidence, 'successful-restore')
                    if restore_resources() != before_restore:
                        raise VerificationError('restore_resources_leaked')
                    restored = json.loads((config.state / 'last-restore.json').read_text())
                    report['checks'].append({'name': 'isolated_postgis_restore_and_content_comparison',
                                              'status': 'passed', **restored})
                    run_backup_cli(config, 'status', evidence, 'freshness-status')
                    before_alerts = len(receiver.events)
                    run_backup_cli(config, 'backup', evidence, 'injected-dump-failure', expect_success=False,
                                   extra_env={'YANHUO_TEST_FAIL_DUMP': '1'})
                    assert_writers_resumed(config, original)
                    if (len(receiver.events) != before_alerts + 1
                            or receiver.events[-1].get('stage') != 'consistent_capture'):
                        raise VerificationError('backup_failure_alert_not_received')
                    if json.loads(config.success.read_text()) != success:
                        raise VerificationError('failure_overwrote_last_success')
                    failure_state = json.loads((config.state / 'last-failure.json').read_text())
                    if not failure_state.get('alert_delivered'):
                        raise VerificationError('backup_failure_not_durably_recorded')
                    report['checks'].append({'name': 'dump_failure_resumes_original_writers_and_alerts', 'status': 'passed'})
                    receiver.fail = True
                    result = run_backup_cli(config, 'backup', evidence, 'injected-alert-failure', expect_success=False,
                                            extra_env={'YANHUO_TEST_FAIL_DUMP': '1'})
                    receiver.fail = False
                    assert_writers_resumed(config, original)
                    if b'failure_alert_not_delivered' not in result.stderr:
                        raise VerificationError('failed_alert_not_recorded')
                    failure_state = json.loads((config.state / 'last-failure.json').read_text())
                    if failure_state.get('alert_delivered') is not False:
                        raise VerificationError('failed_alert_missing_durable_delivery_state')
                    report['failure_record'] = failure_state
                    report['checks'].append({'name': 'alert_failure_recorded_without_leaving_writers_stopped', 'status': 'passed'})
                    with tempfile.TemporaryDirectory(prefix='corruption-', dir=private) as restored_dir:
                        bundle, manifest = backup.restore_bundle(config, Path(restored_dir), 'latest')
                        required = ['payment-secrets/wechat-accounts.json', 'payment-secrets/synthetic-merchant-private.pem',
                                    'media/' + media_path.split('/app/media/', 1)[1]]
                        if not all(path in manifest['files'] for path in required):
                            raise VerificationError('media_or_virtual_payment_files_missing')
                        for table, count in financial_fixture['table_rows'].items():
                            if (count < 1 or table not in manifest['tables']
                                    or manifest.get('table_row_counts', {}).get(table, 0) < count):
                                raise VerificationError('financial_history_missing_from_backup_manifest')
                        report['financial_tables_verified'] = {
                            table: manifest['table_row_counts'][table] for table in financial_fixture['table_rows']}
                        (bundle / required[-1]).write_bytes(b'damaged fixture')
                        try:
                            backup.verify_files(bundle)
                        except backup.BackupError as error:
                            if str(error) != 'restored_file_manifest_mismatch':
                                raise
                        else:
                            raise VerificationError('corrupt_media_was_accepted')
                    report['checks'].append({'name': 'uploaded_media_and_virtual_payment_config_verified_corruption_rejected',
                                              'status': 'passed', 'files_verified': len(manifest['files'])})
                    before_failure = restore_resources()
                    damaged = run_backup_cli(config, 'restore-check', evidence, 'injected-restore-failure',
                        snapshot=success['snapshot_id'], corrupt_restore=True, expect_success=False)
                    if b'test_fault=corrupted_downloaded_dump' not in damaged.stdout:
                        raise VerificationError('corrupt_restore_fault_not_exercised')
                    restore_failure = json.loads((config.state / 'last-restore-failure.json').read_text())
                    if (restore_failure.get('operation') != 'restore-check'
                            or restore_failure.get('stage') != 'restore_load_dump'
                            or restore_failure.get('reason') != 'tool_returned_nonzero'
                            or restore_failure.get('alert_delivered') is not True):
                        raise VerificationError('actual_restore_failure_not_recorded_and_alerted')
                    if restore_resources() != before_failure:
                        raise VerificationError('failed_restore_resources_leaked')
                    failed_monitor = record_restore_monitor(config, evidence, 'monitor-after-restore-failure',
                                                            restore_failed=True)
                    report['checks'].append({'name': 'actual_pg_restore_failure_cleans_owned_container_and_volume', 'status': 'passed'})
                    report['checks'].append({'name': 'actual_restore_failure_is_durable_and_monitored',
                        'status': 'passed', 'issues': failed_monitor['issues'],
                        'evidence': 'monitor-after-restore-failure.json'})
                    retained = retain_fixture_snapshot(config, success['snapshot_id'], marker['run_id'])
                    backup.write_json(evidence / 'retained-good-snapshot.json', retained)
                    run_backup_cli(config, 'backup', evidence, 'successful-backup-after-restore-failure')
                    assert_writers_resumed(config, original)
                    new_capture = json.loads(config.success.read_text())
                    if (datetime.fromisoformat(new_capture['completed_at'])
                            <= datetime.fromisoformat(restore_failure['failed_at'])):
                        raise VerificationError('new_backup_did_not_follow_restore_failure')
                    still_failed = record_restore_monitor(config, evidence, 'monitor-after-new-backup', restore_failed=True)
                    report['checks'].append({'name': 'new_actual_backup_does_not_clear_restore_failure',
                        'status': 'passed', 'issues': still_failed['issues'],
                        'evidence': 'monitor-after-new-backup.json', **new_capture})
                    before_recovery = restore_resources()
                    run_backup_cli(config, 'restore-check', evidence, 'successful-restore-after-failure',
                                   snapshot=retained['retained_snapshot_id'])
                    if restore_resources() != before_recovery:
                        raise VerificationError('recovery_restore_resources_leaked')
                    restored_again = json.loads((config.state / 'last-restore.json').read_text())
                    if restored_again['snapshot_id'] != retained['retained_snapshot_id']:
                        raise VerificationError('recovery_did_not_verify_original_good_snapshot')
                    recovered_monitor = record_restore_monitor(config, evidence, 'monitor-after-restored-snapshot',
                                                               restore_failed=False)
                    if recovered_monitor['issues']:
                        raise VerificationError('backup_monitor_not_healthy_after_restore_recovery')
                    report['checks'].append({'name': 'only_actual_successful_restore_clears_restore_failure',
                        'status': 'passed', 'issues': recovered_monitor['issues'],
                        'evidence': 'monitor-after-restored-snapshot.json', **restored_again})
                    assert_writers_resumed(config, original)
                    recovered = assert_writers_recovered(config, original, evidence)
                    report['checks'].append({'name': 'all_original_writers_healthy_with_post_resume_progress',
                                              'status': 'passed', **recovered})
                    report['alerts'] = receiver.events
            finally:
                cleanup_failures = []
                try:
                    if config and original:
                        # Production context manager normally resumes the writers;
                        # also recover a checkpoint if verification itself failed.
                        backup.resume(config)
                    if media_path and original:
                        command(['docker', 'exec', original['backend'], 'python', '-c',
                                 'import pathlib;pathlib.Path(' + repr(media_path) + ').unlink(missing_ok=True)'])
                except Exception:
                    cleanup_failures.append('writer_or_fixture_cleanup_failed')
                finally:
                    receiver.close()
                    cleanup['receiver_stopped'] = True
                if cleanup_failures:
                    cleanup['errors'] = cleanup_failures
                    raise VerificationError('backup_verification_cleanup_failed')
        report['status'] = 'passed'
    except Exception as error:
        report['status'] = 'failed'
        report['failure'] = str(error) if isinstance(error, (VerificationError, backup.BackupError)) else type(error).__name__
        raise
    finally:
        if overlay_created:
            overlay.unlink(missing_ok=True)
        report['cleanup'] = cleanup
        report['duration_seconds'] = round(time.monotonic() - started, 3)
        report['completed_at'] = backup.utcnow()
        backup.write_json(evidence / 'backup-runtime-report.json', report)
    return report


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--project-dir', required=True)
    parser.add_argument('--project-name', required=True)
    parser.add_argument('--env-file', required=True)
    parser.add_argument('--evidence-dir', required=True)
    args = parser.parse_args()
    try:
        report = run_verification(args.project_dir, args.project_name, args.env_file, args.evidence_dir)
        print(json.dumps({'suite': report['suite'], 'status': report['status'],
                          'scope': report['scope'], 'evidence': str(Path(args.evidence_dir).resolve())}))
        return 0
    except Exception as error:
        reason = str(error) if isinstance(error, (VerificationError, backup.BackupError)) else type(error).__name__
        print(json.dumps({'suite': 'production-backup-runtime', 'status': 'failed', 'reason': reason}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
