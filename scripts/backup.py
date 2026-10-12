"""Single-host, fail-closed restic backups and network-isolated restore drills.

The operator explicitly configures this host tool; importing it has no effects.
No command prints subprocess output, database rows, repository URLs, or secrets.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.parse import urlsplit

WRITERS = ('backend', 'callback', 'worker', 'payment_worker', 'notification_worker')
MANIFEST = 'yanhuo-backup-manifest.json'
STAGE = 'configuration'


class BackupError(RuntimeError):
    """Messages are fixed labels, never subprocess output or credential values."""


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def run(command, *, cwd=None, env=None, output=None, timeout=600):
    try:
        result = subprocess.run([str(x) for x in command], cwd=cwd, env=env,
            stdout=output or subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BackupError('tool_failed_or_timed_out') from error
    if result.returncode:
        raise BackupError('tool_returned_nonzero')
    return result.stdout or b''


def private_file(path):
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise BackupError('required_private_file_missing')
    if os.name != 'nt' and path.stat().st_mode & 0o077:
        raise BackupError('private_file_requires_owner_only_permissions')
    return path


class Config:
    def __init__(self, env=None):
        self.env = dict(os.environ if env is None else env)
        def required(name):
            value = self.env.get(name, '').strip()
            if not value:
                raise BackupError('required_configuration_missing_' + name.lower())
            return value
        self.project = Path(required('BACKUP_PROJECT_DIR')).resolve(strict=True)
        self.name = required('BACKUP_PROJECT_NAME')
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{1,62}', self.name):
            raise BackupError('invalid_compose_project_name')
        if not (self.project / 'compose.yaml').is_file():
            raise BackupError('compose_project_missing')
        self.config = private_file(Path(required('BACKUP_ENV_FILE')))
        self.state = Path(required('BACKUP_STATE_DIR')).resolve()
        if self.state.is_relative_to(self.project):
            raise BackupError('backup_state_must_be_outside_checkout')
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != 'nt' and self.state.stat().st_mode & 0o077:
            raise BackupError('backup_state_requires_owner_only_permissions')
        repository = required('RESTIC_REPOSITORY')
        if not repository.startswith(('s3:https://', 'sftp:', 'rest:https://', 'b2:', 'azure:', 'gs:')):
            raise BackupError('offsite_repository_required')
        if required('BACKUP_OFFSITE_CONFIRMED') != 'true':
            raise BackupError('independent_storage_must_be_confirmed')
        private_file(Path(required('RESTIC_PASSWORD_FILE')))
        self.alert = private_file(Path(required('BACKUP_FAILURE_WEBHOOK_FILE')))
        webhook = urlsplit(self.alert.read_text(encoding='utf-8').strip())
        if webhook.scheme != 'https' or not webhook.hostname or webhook.username or webhook.password:
            raise BackupError('failure_webhook_requires_https')
        self.payments = required('BACKUP_PAYMENTS_CONFIGURED')
        if self.payments not in ('true', 'false'):
            raise BackupError('payments_configuration_must_be_explicit')
        self.payment_dir = None
        if self.payments == 'true':
            self.payment_dir = Path(required('WECHAT_PAY_SECRETS_DIR')).resolve(strict=True)
            if self.payment_dir.is_relative_to(self.project) or not self.payment_dir.is_dir():
                raise BackupError('payment_secrets_must_be_outside_checkout')
            private_file(self.payment_dir / 'wechat-accounts.json')
        self.compose = ['docker', 'compose', '--project-directory', self.project,
            '--project-name', self.name, '--env-file', self.config, '-f', self.project / 'compose.yaml']
        if self.payments == 'true':
            self.compose += ['-f', self.project / 'compose.payments.yaml']
        self.tag = 'yanhuo-' + self.name
        self.maintenance = self.state / 'maintenance.json'
        self.success = self.state / 'last-success.json'
        self.rpo = int(self.env.get('BACKUP_RPO_SECONDS', '3600'))
        self.rto = int(self.env.get('BACKUP_RTO_SECONDS', '7200'))
        if self.rpo < 60 or self.rto < 60:
            raise BackupError('invalid_recovery_targets')


def write_json(path, data):
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        if os.name != 'nt':
            os.chmod(temporary, 0o600)
        json.dump(data, stream, ensure_ascii=False, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def record_failure(config, failure):
    """Keep restore failures independent from later capture/status failures.

    The legacy file remains available to existing operators. Preserve a restore
    failure written by an older version before replacing that shared record.
    Only a later successful restore-check can establish recoverability again.
    """
    latest = config.state / 'last-failure.json'
    restore = config.state / 'last-restore-failure.json'
    if failure['operation'] == 'restore-check':
        write_json(restore, failure)
    elif latest.exists() and not restore.exists():
        try:
            previous = json.loads(latest.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            # A new known failure must still be recorded if legacy state is
            # unreadable. Do not treat that state as a successful restoration.
            previous = None
        if isinstance(previous, dict) and previous.get('operation') == 'restore-check':
            # Failure to preserve a known restore incident must leave the
            # original shared record intact, rather than silently erase it.
            write_json(restore, previous)
    write_json(latest, failure)


@contextmanager
def exclusive(config):
    # Kernel locks disappear after a crash; no stale PID can unlock another run.
    path = config.state / 'task.lock'
    with path.open('a+b') as stream:
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            if path.stat().st_size == 0:
                stream.write(b'0'); stream.flush(); stream.seek(0)
            try: msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error: raise BackupError('backup_already_running') from error
        else:
            import fcntl
            try: fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error: raise BackupError('backup_already_running') from error
        yield


def owned_container(config, identifier, expected=None):
    labels = json.loads(run(['docker', 'inspect', '--format', '{{json .Config.Labels}}', identifier]))
    if (labels.get('com.docker.compose.project') != config.name
            or Path(labels.get('com.docker.compose.project.working_dir', '')).resolve() != config.project
            or labels.get('com.docker.compose.service') not in (expected or WRITERS)):
        raise BackupError('container_ownership_mismatch')
    return labels['com.docker.compose.service']


def containers(config, services):
    ids = run([*config.compose, 'ps', '--status', 'running', '--quiet', *services], env=config.env).decode().split()
    result = {}
    for identifier in ids:
        service = owned_container(config, identifier, services)
        if service in result:
            raise BackupError('backup_requires_single_instance_per_service')
        result[service] = identifier
    return result


def database_identity(config, identifier):
    """Use the verified database service's explicit identity, never a guessed DB.

    Different isolated deployments can use different database names. The
    Compose database container, already checked by project and working directory,
    is the source of truth. Values remain subprocess arguments, never SQL/shell.
    """
    owned_container(config, identifier, ('db',))
    entries = json.loads(run(['docker', 'inspect', '--format', '{{json .Config.Env}}', identifier]))
    if not isinstance(entries, list) or any(not isinstance(entry, str) for entry in entries):
        raise BackupError('invalid_database_identity_configuration')
    selected = {}
    for entry in entries:
        key, _, value = entry.partition('=')
        if key not in ('POSTGRES_USER', 'POSTGRES_DB'):
            continue
        if key in selected:
            raise BackupError('duplicate_database_identity_configuration')
        selected[key] = value
    if set(selected) != {'POSTGRES_USER', 'POSTGRES_DB'}:
        raise BackupError('explicit_database_identity_required')
    if any(not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]{0,62}', value) for value in selected.values()):
        raise BackupError('unsupported_database_identity')
    return selected['POSTGRES_USER'], selected['POSTGRES_DB']


def resume(config):
    if not config.maintenance.exists():
        return
    state = json.loads(config.maintenance.read_text(encoding='utf-8'))
    if state.get('project') != config.name or not isinstance(state.get('containers'), list):
        raise BackupError('invalid_maintenance_checkpoint')
    for identifier in state['containers']:
        owned_container(config, identifier)
    if state['containers']:
        run(['docker', 'start', *state['containers']], env=config.env, timeout=120)
    config.maintenance.unlink()


@contextmanager
def stopped_writers(config):
    if config.env.get('BACKUP_MAINTENANCE_APPROVED') != 'true':
        raise BackupError('maintenance_window_not_enabled')
    if config.maintenance.exists():
        raise BackupError('unfinished_maintenance_run_resume_first')
    running = containers(config, WRITERS)
    if 'backend' not in running:
        raise BackupError('running_backend_required')
    write_json(config.maintenance, {'project': config.name, 'started_at': utcnow(),
        'containers': list(running.values())})
    try:
        # IDs were checked against both project and working-directory labels.
        run(['docker', 'stop', '--time', '30', *running.values()], env=config.env, timeout=180)
        yield running
    finally:
        resume(config)


def fingerprint(db_id, user='yanhuo', database='yanhuo', *, row_counts=None):
    base = ['docker', 'exec', db_id, 'psql', '-XAt', '-U', user, '-d', database]
    names = run([*base, '-c', "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"]).decode().splitlines()
    result = {}
    for name in names:
        if not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]*', name):
            raise BackupError('unsupported_database_identifier')
        rows = run([*base, '-c', f'SET TIME ZONE \'UTC\'; SELECT row_to_json(t)::text FROM public."{name}" t ORDER BY row_to_json(t)::text'], timeout=600)
        result[name] = hashlib.sha256(rows).hexdigest()
        if row_counts is not None:
            # row_to_json produces exactly one JSON object per output line;
            # embedded newlines are escaped. Ignore psql's SET command status.
            row_counts[name] = sum(line.startswith(b'{') for line in rows.splitlines())
    return result


def hash_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def file_manifest(root):
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise BackupError('backup_symlinks_are_not_supported')
        if path.is_file() and path != root / MANIFEST:
            result[path.relative_to(root).as_posix()] = {'bytes': path.stat().st_size, 'sha256': hash_file(path)}
    return result


def verify_files(root):
    manifest = json.loads((root / MANIFEST).read_text(encoding='utf-8'))
    if manifest.get('version') != 1 or manifest.get('files') != file_manifest(root):
        raise BackupError('restored_file_manifest_mismatch')
    if 'database.dump' not in manifest['files'] or not manifest.get('tables'):
        raise BackupError('incomplete_restore_bundle')
    if manifest['payments_configured'] and 'payment-secrets/wechat-accounts.json' not in manifest['files']:
        raise BackupError('restored_payment_configuration_missing')
    return manifest


def restic(config, *args, cwd=None, timeout=1800):
    return run(['restic', *args], env=config.env, cwd=cwd, timeout=timeout)


def restore_bundle(config, target, snapshot):
    restic(config, 'restore', snapshot, '--target', target, '--verify', timeout=config.rto)
    matches = list(target.rglob(MANIFEST))
    if len(matches) != 1:
        raise BackupError('restore_manifest_missing_or_ambiguous')
    root = matches[0].parent
    return root, verify_files(root)


def selected_snapshot(config, requested):
    snapshots = json.loads(restic(config, 'snapshots', '--json', '--tag', config.tag, '--host', config.name))
    if not snapshots:
        raise BackupError('no_backup_snapshot')
    if requested == 'latest':
        return max(snapshots, key=lambda row: row['time'])['id']
    matches = [row['id'] for row in snapshots if row['id'] == requested]
    if len(matches) != 1:
        raise BackupError('snapshot_not_owned_by_project')
    return matches[0]


def backup(config):
    global STAGE
    started = time.monotonic()
    if config.maintenance.exists():
        raise BackupError('unfinished_maintenance_run_resume_first')
    # Check credentials and repository before opening the maintenance window.
    STAGE = 'repository_preflight'
    restic(config, 'snapshots', '--json', timeout=120)
    database = containers(config, ('db',))
    if 'db' not in database:
        raise BackupError('running_database_required')
    database_user, database_name = database_identity(config, database['db'])
    with tempfile.TemporaryDirectory(prefix='backup-', dir=config.state) as temporary:
        bundle = Path(temporary) / 'bundle'
        bundle.mkdir(mode=0o700)
        STAGE = 'consistent_capture'
        with stopped_writers(config) as running:
            with (bundle / 'database.dump').open('wb') as output:
                run(['docker', 'exec', database['db'], 'pg_dump', '-U', database_user,
                     '-d', database_name, '-Fc'], output=output)
            table_rows = {}
            tables = fingerprint(database['db'], database_user, database_name, row_counts=table_rows)
            (bundle / 'media').mkdir()
            run(['docker', 'cp', running['backend'] + ':/app/media/.', str(bundle / 'media')])
            shutil.copy2(config.config, bundle / 'deployment.env')
            if config.payment_dir:
                # Verify before copying: copytree must never dereference an unknown link.
                file_manifest(config.payment_dir)
                shutil.copytree(config.payment_dir, bundle / 'payment-secrets')
            shutil.copy2(config.project / 'compose.yaml', bundle / 'compose.yaml')
            if config.payments == 'true':
                shutil.copy2(config.project / 'compose.payments.yaml', bundle / 'compose.payments.yaml')
            images = json.loads(run(['docker', 'inspect', '--format', '{{json .Image}}', running['backend']]))
            captured_at = utcnow()
            write_json(bundle / MANIFEST, {'version': 1, 'created_at': captured_at, 'project': config.name,
                'backend_image_id': images, 'payments_configured': config.payments == 'true',
                'database_user': database_user, 'database_name': database_name,
                'tables': tables, 'table_row_counts': table_rows, 'files': file_manifest(bundle)})
        STAGE = 'encrypted_upload'
        lines = restic(config, 'backup', '--json', '--host', config.name, '--tag', config.tag, '.', cwd=bundle).decode().splitlines()
        summary = [json.loads(line) for line in lines if line and json.loads(line).get('message_type') == 'summary']
        if len(summary) != 1 or not summary[0].get('snapshot_id'):
            raise BackupError('backup_snapshot_not_confirmed')
        snapshot = summary[0]['snapshot_id']
        STAGE = 'encrypted_round_trip'
        restored = Path(temporary) / 'round-trip'
        restored.mkdir()
        restore_bundle(config, restored, snapshot)
        restic(config, 'check')
        STAGE = 'retention'
        retention = []
        for unit, default in [('hourly', '48'), ('daily', '14'), ('weekly', '8'), ('monthly', '12')]:
            count = int(config.env.get('BACKUP_KEEP_' + unit.upper(), default))
            if count < 1:
                raise BackupError('retention_must_keep_positive_generations')
            retention += ['--keep-' + unit, str(count)]
        restic(config, 'forget', '--prune', '--group-by', 'host,tags', '--host', config.name, '--tag', config.tag, *retention)
        if (datetime.now(timezone.utc) - datetime.fromisoformat(captured_at)).total_seconds() > config.rpo:
            raise BackupError('uploaded_snapshot_already_exceeds_rpo')
        write_json(config.success, {'completed_at': utcnow(), 'captured_at': captured_at, 'snapshot_id': snapshot,
            'duration_seconds': round(time.monotonic() - started, 2), 'file_round_trip': True,
            'postgres_restore_drill': 'separate_restore_check_required'})
    print(json.dumps({'status': 'ok', 'operation': 'backup', 'file_round_trip': True}))


def restore_check(config, requested):
    global STAGE
    STAGE = 'isolated_restore'
    started = time.monotonic()
    snapshot = selected_snapshot(config, requested)
    with tempfile.TemporaryDirectory(prefix='restore-', dir=config.state) as temporary:
        bundle, manifest = restore_bundle(config, Path(temporary), snapshot)
        suffix = secrets.token_hex(8)
        name, volume = 'yanhuo-restore-' + suffix, 'yanhuo-restore-data-' + suffix
        label = 'yanhuo.restore-run=' + suffix
        run(['docker', 'volume', 'create', '--label', label, volume])
        container = None
        try:
            env = {**config.env, 'POSTGRES_PASSWORD': secrets.token_hex(32)}
            container = run(['docker', 'create', '--network', 'none', '--name', name, '--label', label,
                '--env', 'POSTGRES_PASSWORD', '--env', 'POSTGRES_USER=yanhuo', '--mount', f'type=volume,source={volume},target=/var/lib/postgresql/data',
                '--mount', f'type=bind,source={bundle / "database.dump"},target=/restore/database.dump,readonly',
                'postgis/postgis:17-3.5'], env=env).decode().strip()
            run(['docker', 'start', container])
            STAGE = 'restore_database_readiness'
            for attempt in range(60):
                try:
                    # The image first starts a Unix-only temporary server for
                    # init scripts, then shuts it down. TCP is enabled only by
                    # the final server; do not begin restoring into that first
                    # server and lose the connection midway through pg_restore.
                    run(['docker', 'exec', container, 'pg_isready', '-h', '127.0.0.1', '-U', 'yanhuo'], timeout=5)
                    break
                except BackupError:
                    time.sleep(1)
            else:
                raise BackupError('restore_database_not_ready')
            STAGE = 'restore_create_database'
            run(['docker', 'exec', container, 'createdb', '-U', 'yanhuo', 'yanhuo_restore_check'])
            STAGE = 'restore_load_dump'
            run(['docker', 'exec', container, 'pg_restore', '-U', 'yanhuo', '--exit-on-error', '--no-owner',
                '-d', 'yanhuo_restore_check', '/restore/database.dump'], timeout=config.rto)
            STAGE = 'restore_content_check'
            if fingerprint(container, 'yanhuo', 'yanhuo_restore_check') != manifest['tables']:
                raise BackupError('restored_database_content_mismatch')
        finally:
            # Unique labels and IDs: never discover or delete by a shared name prefix.
            if container:
                actual = run(['docker', 'inspect', '--format', '{{index .Config.Labels "yanhuo.restore-run"}}', container]).decode().strip()
                if actual != suffix:
                    raise BackupError('restore_container_ownership_mismatch')
                run(['docker', 'rm', '--force', container])
            actual = run(['docker', 'volume', 'inspect', '--format', '{{index .Labels "yanhuo.restore-run"}}', volume]).decode().strip()
            if actual != suffix:
                raise BackupError('restore_volume_ownership_mismatch')
            run(['docker', 'volume', 'rm', volume])
        elapsed = time.monotonic() - started
        STAGE = 'restore_rto_check'
        if elapsed > config.rto:
            raise BackupError('restore_exceeded_rto_target')
        write_json(config.state / 'last-restore.json', {'completed_at': utcnow(), 'snapshot_id': snapshot,
            'duration_seconds': round(elapsed, 2), 'tables_verified': len(manifest['tables']),
            'files_verified': len(manifest['files']), 'gateway_network': 'disabled'})
    print(json.dumps({'status': 'ok', 'operation': 'restore-check', 'database_and_files_verified': True}))


def status(config):
    if config.maintenance.exists():
        raise BackupError('unfinished_maintenance_run_resume_first')
    if not config.success.is_file():
        raise BackupError('no_successful_backup')
    state = json.loads(config.success.read_text(encoding='utf-8'))
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(state['captured_at'])).total_seconds()
    if age < -60 or age > config.rpo:
        raise BackupError('backup_rpo_target_exceeded')
    print(json.dumps({'status': 'ok', 'operation': 'status', 'backup_age_seconds': round(age, 1)}))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def alert(env, stage):
    path = private_file(Path(env.get('BACKUP_FAILURE_WEBHOOK_FILE', '')))
    url = path.read_text(encoding='utf-8').strip()
    if not url.startswith('https://'):
        raise BackupError('failure_webhook_requires_https')
    body = json.dumps({'service': 'yanhuo-backup', 'status': 'failed', 'stage': stage, 'time': utcnow()}).encode()
    request = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json'})
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=15) as response:
        if not 200 <= response.status < 300:
            raise BackupError('failure_alert_not_delivered')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['backup', 'status', 'restore-check', 'resume', 'alert'])
    parser.add_argument('--snapshot', default='latest', help='Full snapshot id or latest within this project only.')
    options = parser.parse_args()
    config = None
    try:
        if options.operation == 'alert':
            alert(os.environ, 'systemd_job_failed_or_timed_out')
            return 0
        config = Config()
        with exclusive(config):
            if options.operation == 'backup': backup(config)
            elif options.operation == 'restore-check': restore_check(config, options.snapshot)
            elif options.operation == 'resume': resume(config)
            else: status(config)
    except Exception as error:
        # The reason is a fixed internal code; never include arbitrary exception text.
        reason = str(error) if isinstance(error, BackupError) else type(error).__name__
        print(json.dumps({'status': 'failed', 'stage': STAGE, 'reason': reason}), file=sys.stderr)
        failure = {'failed_at': utcnow(), 'operation': options.operation, 'stage': STAGE,
                   'reason': reason, 'alert_delivered': False}
        # A failed webhook must not erase the failure from host monitoring. Only
        # write after Config validated the private, external state directory.
        if config is not None:
            try: record_failure(config, failure)
            except OSError:
                print('{"status":"failed","reason":"failure_state_not_written"}', file=sys.stderr)
        try:
            alert(os.environ, STAGE)
            failure['alert_delivered'] = True
        except Exception:
            print('{"status":"failed","reason":"failure_alert_not_delivered"}', file=sys.stderr)
        if config is not None and failure['alert_delivered']:
            try: record_failure(config, failure)
            except OSError:
                print('{"status":"failed","reason":"failure_state_not_written"}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
