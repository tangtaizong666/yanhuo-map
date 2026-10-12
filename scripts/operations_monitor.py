"""Read-only host checks with a durable, secret-free HTTPS alert outbox.

No orders, payments, containers, or backups are changed by this program.
Delivery is at least once: receivers should deduplicate the stable event_id.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import secrets
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit

WORKERS = ('expire_orders', 'reconcile_payments', 'process_payment_notifications')
SERVICES = ('db', 'backend', 'callback', 'worker', 'payment_worker', 'notification_worker', 'web')
CATEGORIES = {
    'containers_unavailable', 'containers_unhealthy', 'release_failed', 'operations_unavailable',
    *(f'worker_{worker}' for worker in WORKERS),
    'notifications_backlog', 'notifications_dead', 'notifications_conflicts',
    'payments_backlog', 'payments_review', 'backup_stale', 'backup_unavailable',
    'backup_maintenance', 'backup_failed', 'restore_failed', 'orders_expiry_backlog',
}
UNAVAILABLE_SOURCES = {
    'operations_unavailable': {*('worker_' + name for name in WORKERS),
                              'notifications_backlog', 'notifications_dead', 'notifications_conflicts',
                              'payments_backlog', 'payments_review', 'orders_expiry_backlog'},
    'containers_unavailable': {'containers_unhealthy', 'release_failed'},
    'backup_unavailable': {'backup_stale', 'backup_maintenance', 'backup_failed', 'restore_failed'},
}


class MonitorError(RuntimeError):
    """Only fixed, non-sensitive error codes belong in this exception."""


def timestamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def epoch(value):
    date = datetime.fromisoformat(value)
    if date.tzinfo is None:
        raise ValueError('timezone_required')
    return date.timestamp()


def private_file(value, *, outside):
    path = Path(value)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise MonitorError('private_file_required')
    path = path.resolve()
    if path.is_relative_to(outside):
        raise MonitorError('private_configuration_must_be_outside_checkout')
    if os.name != 'nt' and path.stat().st_mode & 0o077:
        raise MonitorError('private_file_requires_owner_only_permissions')
    return path


def read_json(path):
    if path.is_symlink() or path.stat().st_size > 4 * 1024 * 1024:
        raise MonitorError('invalid_state_file')
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path, value):
    temporary = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != 'nt':
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


class Config:
    def __init__(self, filename):
        try:
            self.filename = Path(filename)
            values = read_json(self.filename)
            self.project = Path(values['project_dir']).resolve(strict=True)
            private_file(str(self.filename), outside=self.project)
            self.name = values['project_name']
            if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{1,62}', self.name):
                raise MonitorError('invalid_project_name')
            self.env_file = private_file(values['env_file'], outside=self.project)
            self.webhook_file = private_file(values['webhook_file'], outside=self.project)
            self.webhook = self.webhook_file.read_text(encoding='utf-8').strip()
            target = urlsplit(self.webhook)
            if target.scheme != 'https' or not target.hostname or target.username or target.password or target.fragment:
                raise MonitorError('webhook_requires_https_without_userinfo')
            self.state = Path(values['state_dir']).resolve()
            self.backup = Path(values['backup_state_dir']).resolve()
            if self.state.is_relative_to(self.project) or self.backup.is_relative_to(self.project):
                raise MonitorError('state_must_be_outside_checkout')
            self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
            if os.name != 'nt' and self.state.stat().st_mode & 0o077:
                raise MonitorError('state_requires_owner_only_permissions')
            self.ca_file = str(private_file(values['ca_file'], outside=self.project)) if values.get('ca_file') else None
            self.heartbeat_age = int(values.get('heartbeat_seconds', 120))
            self.backlog_age = int(values.get('backlog_seconds', 600))
            self.expiry_age = int(values.get('expiry_backlog_seconds', 120))
            self.backup_age = int(values.get('backup_rpo_seconds', 3600))
            self.reminder = int(values.get('reminder_seconds', 1800))
            if min(self.heartbeat_age, self.backlog_age, self.expiry_age, self.backup_age, self.reminder) < 60:
                raise MonitorError('threshold_below_one_minute')
            self.runbook = values.get('runbook', 'docs/operations-monitor.md')
            if not isinstance(self.runbook, str) or len(self.runbook) > 512 or any(c in self.runbook for c in '\r\n'):
                raise MonitorError('invalid_runbook')
            files = values.get('compose_files', ['compose.yaml'])
            if not isinstance(files, list) or not files:
                raise MonitorError('invalid_compose_files')
            self.compose = ['docker', 'compose', '--project-directory', str(self.project),
                            '--project-name', self.name, '--env-file', str(self.env_file)]
            for filename in files:
                candidate = (self.project / filename).resolve(strict=True)
                if not candidate.is_relative_to(self.project) or not candidate.is_file():
                    raise MonitorError('compose_file_outside_project')
                self.compose.extend(['-f', str(candidate)])
            self.store = self.state / 'monitor-state.json'
        except MonitorError:
            raise
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise MonitorError('invalid_monitor_configuration') from error


@contextmanager
def exclusive(config):
    path = config.state / 'monitor.lock'
    if path.is_symlink():
        raise MonitorError('invalid_lock_file')
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, 'a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            stream.seek(0)
            if path.stat().st_size == 0:
                stream.write(b'0')
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise MonitorError('monitor_already_running') from error
        else:
            import fcntl
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise MonitorError('monitor_already_running') from error
        yield


def run(command):
    try:
        return subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=30, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise MonitorError('check_command_failed') from error


def container_issues(config):
    try:
        result = run([*config.compose, 'ps', '--all', '--quiet'])
        identifiers = result.stdout.decode().split()
        if result.returncode or not identifiers or any(not re.fullmatch('[a-f0-9]{12,64}', value) for value in identifiers):
            raise MonitorError('container_query_failed')
        result = run(['docker', 'inspect', '--format',
                      '{"state":{{json .State}},"labels":{{json .Config.Labels}}}', *identifiers])
        if result.returncode:
            raise MonitorError('container_query_failed')
        records = [json.loads(line) for line in result.stdout.decode().splitlines()]
        observed = {}
        for record in records:
            labels = record['labels']
            if labels.get('com.docker.compose.project') != config.name or Path(labels.get('com.docker.compose.project.working_dir', '')).resolve() != config.project:
                raise MonitorError('container_ownership_mismatch')
            service = labels.get('com.docker.compose.service')
            observed.setdefault(service, []).append(record['state'])
        unhealthy = 0
        for service in SERVICES:
            states = observed.get(service, [])
            if len(states) != 1 or not states[0].get('Running') or states[0].get('Health', {}).get('Status', 'healthy') != 'healthy':
                unhealthy += 1
        issues = {'containers_unhealthy': unhealthy} if unhealthy else {}
        releases = observed.get('release', [])
        if len(releases) != 1 or releases[0].get('Status') != 'exited' or releases[0].get('ExitCode') != 0:
            issues['release_failed'] = 1
        return issues
    except (MonitorError, UnicodeError, ValueError, TypeError, KeyError):
        return {'containers_unavailable': 1}


def integer(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError('invalid_count')
    return value


def business_issues(config):
    try:
        result = run([*config.compose, 'exec', '-T', 'backend', 'python', 'manage.py', 'check_operations',
                      '--max-heartbeat-age', str(config.heartbeat_age), '--max-payment-age', str(config.backlog_age),
                      '--max-expiry-age', str(config.expiry_age)])
        record = json.loads(result.stdout)
        if record['status'] not in ('ok', 'unavailable', 'attention') or result.returncode not in (0, 1):
            raise ValueError('invalid_operations_result')
        workers = record['workers']
        if len(workers) != len(WORKERS) or {worker['name'] for worker in workers} != set(WORKERS):
            raise ValueError('missing_worker')
        issues = {}
        for worker in workers:
            if worker['healthy'] is not True:
                issues['worker_' + worker['name']] = 1
        notifications = record['notifications']
        for key in ('dead', 'conflicts'):
            count = integer(notifications[key])
            if count:
                issues['notifications_' + key] = count
        if notifications['overdue']:
            issues['notifications_backlog'] = max(1, integer(notifications['pending']))
        payments = record['payments']
        if payments['overdue']:
            issues['payments_backlog'] = max(1, integer(payments['pending']) + integer(payments['refunds_pending']))
        if integer(payments['reviews_required']):
            issues['payments_review'] = payments['reviews_required']
        expiry = record['order_expiry']
        eligible, overdue = integer(expiry['eligible_pending']), integer(expiry['overdue_count'])
        oldest = expiry['oldest_overdue_seconds']
        if (not isinstance(expiry['overdue'], bool) or overdue > eligible
                or expiry['overdue'] != bool(overdue)
                or (eligible == 0 and oldest is not None)
                or (eligible and (isinstance(oldest, bool) or not isinstance(oldest, (int, float))
                                  or not math.isfinite(oldest) or oldest < 0))):
            raise ValueError('invalid_order_expiry_result')
        if expiry['overdue']:
            issues['orders_expiry_backlog'] = overdue
        if not issues and (record['status'] != 'ok' or result.returncode):
            raise ValueError('inconsistent_operations_result')
        return issues
    except (MonitorError, UnicodeError, ValueError, TypeError, KeyError):
        return {'operations_unavailable': 1}


def backup_issues(config, now):
    issues = {}
    success = config.backup / 'last-success.json'
    completed = 0
    try:
        record = read_json(success)
        age = now - epoch(record['captured_at'])
        completed = epoch(record['completed_at'])
        if age < -60 or completed > now + 60:
            raise ValueError('future_backup')
        if age > config.backup_age:
            issues['backup_stale'] = 1
    except (OSError, MonitorError, ValueError, TypeError, KeyError):
        issues['backup_unavailable'] = 1
    if (config.backup / 'maintenance.json').exists():
        issues['backup_maintenance'] = 1
    failure = config.backup / 'last-failure.json'
    restore_failures = []
    if failure.exists():
        try:
            failure_record = read_json(failure)
            if not isinstance(failure_record, dict):
                raise ValueError('invalid_backup_failure')
            if failure_record.get('operation') == 'restore-check':
                restore_failures.append(epoch(failure_record['failed_at']))
            elif epoch(failure_record['failed_at']) > completed:
                issues['backup_failed'] = 1
        except (OSError, MonitorError, ValueError, TypeError, KeyError):
            issues['backup_failed'] = 1
    restore_failure = config.backup / 'last-restore-failure.json'
    if restore_failure.exists():
        try:
            record = read_json(restore_failure)
            if record['operation'] != 'restore-check':
                raise ValueError('invalid_restore_failure')
            restore_failures.append(epoch(record['failed_at']))
        except (OSError, MonitorError, ValueError, TypeError, KeyError):
            issues['restore_failed'] = 1
    if restore_failures:
        try:
            restored = read_json(config.backup / 'last-restore.json')
            restored_at = epoch(restored['completed_at'])
            if (restored_at > now + 60 or restored_at <= max(restore_failures)
                    or integer(restored['tables_verified']) < 1 or integer(restored['files_verified']) < 1
                    or not isinstance(restored['snapshot_id'], str) or not restored['snapshot_id']
                    or restored['gateway_network'] != 'disabled'):
                raise ValueError('restore_not_verified_after_failure')
        except (OSError, MonitorError, ValueError, TypeError, KeyError):
            issues['restore_failed'] = 1
    return issues


def collect(config, now):
    containers = container_issues(config)
    # A wrong project-directory label must not be followed by exec into that
    # project's backend. Failure to establish ownership also makes its business
    # status unknown, even though the management command itself is read-only.
    business = {'operations_unavailable': 1} if 'containers_unavailable' in containers else business_issues(config)
    return {**containers, **business, **backup_issues(config, now)}


def initial_state():
    return {'version': 1, 'incidents': {}, 'outbox': [], 'deliveries': []}


def load_state(config):
    if not config.store.exists():
        return initial_state()
    try:
        state = read_json(config.store)
        if state['version'] != 1 or not isinstance(state['incidents'], dict) or not isinstance(state['outbox'], list) or not isinstance(state['deliveries'], list):
            raise ValueError('invalid_state')
        for category, incident in state['incidents'].items():
            if category not in CATEGORIES or not isinstance(incident['active'], bool):
                raise ValueError('invalid_incident')
            epoch(incident['first_seen_at'])
            epoch(incident['last_enqueued_at'])
        for event in state['outbox']:
            payload = event['payload']
            if set(payload) != {'event_id', 'kind', 'category', 'count', 'observed_at', 'first_seen_at', 'runbook'}:
                raise ValueError('invalid_payload_fields')
            if payload['category'] not in CATEGORIES or not re.fullmatch('[a-f0-9]{32}', payload['event_id']) or payload['kind'] not in ('problem', 'reminder', 'recovery'):
                raise ValueError('invalid_event')
            integer(payload['count'])
            epoch(payload['observed_at'])
            epoch(payload['first_seen_at'])
            integer(event['attempts'])
            epoch(event['next_attempt_at'])
        return state
    except (OSError, MonitorError, ValueError, TypeError, KeyError) as error:
        # Never silently reset corrupt state: that would re-send every alert.
        raise MonitorError('monitor_state_invalid_preserved') from error


def reconcile(config, state, issues, now):
    unknown = set().union(*(categories for source, categories in UNAVAILABLE_SOURCES.items() if source in issues))
    for category in sorted(set(state['incidents']) | set(issues)):
        if category in unknown and category not in issues:
            # An unreadable source is not evidence of recovery. Preserve the
            # prior incident until a later successful check observes its state.
            continue
        count = issues.get(category, 0)
        previous = state['incidents'].get(category)
        kind = None
        if count and (not previous or not previous['active']):
            previous = {'active': True, 'first_seen_at': timestamp(now), 'count': count, 'last_enqueued_at': timestamp(now)}
            state['incidents'][category] = previous
            kind = 'problem'
        elif count:
            previous['count'] = count
            pending = any(event['payload']['category'] == category for event in state['outbox'])
            last_alert = max(epoch(previous['last_enqueued_at']), epoch(previous.get('last_delivered_at', previous['last_enqueued_at'])))
            if not pending and now - last_alert >= config.reminder:
                kind = 'reminder'
        elif previous and previous['active']:
            previous['active'] = False
            previous['count'] = 0
            kind = 'recovery'
        if kind:
            previous['last_enqueued_at'] = timestamp(now)
            payload = {'event_id': secrets.token_hex(16), 'kind': kind, 'category': category,
                       'count': count, 'observed_at': timestamp(now), 'first_seen_at': previous['first_seen_at'],
                       'runbook': config.runbook + '#' + category.replace('_', '-')}
            state['outbox'].append({'payload': payload, 'attempts': 0, 'next_attempt_at': timestamp(now), 'last_error': ''})
    state['checked_at'] = timestamp(now)
    write_json(config.store, state)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def deliver(config, payload):
    context = ssl.create_default_context(cafile=config.ca_file)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(), urllib.request.HTTPSHandler(context=context))
    request = urllib.request.Request(config.webhook, data=json.dumps(payload, separators=(',', ':')).encode(),
                                    headers={'Content-Type': 'application/json', 'Idempotency-Key': payload['event_id']}, method='POST')
    try:
        with opener.open(request, timeout=5) as response:
            if not 200 <= response.status < 300:
                raise MonitorError('webhook_non_success')
    except (OSError, urllib.error.URLError) as error:
        raise MonitorError('webhook_delivery_failed') from error


def drain(config, state, now):
    delivered = failed = 0
    # Bound one timer run to at most ten HTTPS calls. A failed endpoint is tried
    # once per run, preserving order and avoiding a burst for every category.
    for event in list(state['outbox'])[:10]:
        if epoch(event['next_attempt_at']) > now:
            break
        event['attempts'] += 1
        event['next_attempt_at'] = timestamp(now + min(900, 60 * 2 ** min(event['attempts'] - 1, 4)))
        write_json(config.store, state)  # Persist intent before crossing the network.
        try:
            deliver(config, event['payload'])
        except MonitorError:
            failed += 1
            event['last_error'] = 'webhook_delivery_failed'
            write_json(config.store, state)
            break
        state['outbox'].remove(event)
        if event['payload']['kind'] in ('problem', 'reminder'):
            state['incidents'][event['payload']['category']]['last_delivered_at'] = timestamp(now)
        state['deliveries'].append({'event_id': event['payload']['event_id'], 'category': event['payload']['category'],
                                    'kind': event['payload']['kind'], 'delivered_at': timestamp(now), 'attempts': event['attempts']})
        state['deliveries'] = state['deliveries'][-256:]
        write_json(config.store, state)
        delivered += 1
    return delivered, failed


def check(config, *, now=None, collector=collect):
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    with exclusive(config):
        state = load_state(config)
        issues = collector(config, now)
        if any(category not in CATEGORIES or integer(count) < 1 for category, count in issues.items()):
            raise MonitorError('invalid_issue_categories')
        reconcile(config, state, issues, now)
        delivered, failed = drain(config, state, now)
        report = {'version': 1, 'checked_at': timestamp(now), 'status': 'attention' if issues else 'ok',
                  'issues': issues, 'delivery': {'delivered': delivered, 'failed': failed, 'pending': len(state['outbox'])},
                  'thresholds_seconds': {'heartbeat': config.heartbeat_age, 'backlog': config.backlog_age,
                                         'order_expiry': config.expiry_age, 'backup_rpo': config.backup_age,
                                         'reminder': config.reminder}}
        if failed or state['outbox']:
            report['status'] = 'delivery_pending'
        write_json(config.state / 'last-check.json', report)
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['check'])
    parser.add_argument('--config', required=True, help='Absolute path to private JSON outside the checkout')
    options = parser.parse_args(argv)
    try:
        report = check(Config(options.config))
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0 if report['status'] == 'ok' else 1
    except (MonitorError, OSError, ValueError, TypeError) as error:
        code = str(error) if isinstance(error, MonitorError) else 'monitor_io_or_state_failed'
        print(json.dumps({'status': 'failed', 'code': code}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
