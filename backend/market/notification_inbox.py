"""Bounded notification ingestion and crash-recoverable database delivery.

Ingestion never locks an order or stores an order FK, decrypted payer identity,
signature, or credentials. Workers retain the existing financial lock order.
"""
import hashlib
import json
import logging
import re
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import connection, transaction
from django.db.models import F, Q
from django.utils import timezone

from .notification_models import PaymentNotification
from .wechatpay import GatewayError

MAX_NOTIFICATION_BYTES = 2 * 1024 * 1024
EVENT_TYPES = ('TRANSACTION.SUCCESS', 'REFUND.SUCCESS', 'REFUND.ABNORMAL', 'REFUND.CLOSED')
RESOURCE_FIELDS = ('appid', 'mchid', 'out_trade_no', 'transaction_id', 'trade_state',
    'out_refund_no', 'refund_id', 'refund_status', 'success_time')


def invalid(code='INVALID_PAYMENT_NOTIFICATION'):
    return GatewayError(code, '支付通知未通过校验。', retryable=False)


def minimal_resource(resource):
    if not isinstance(resource, dict):
        raise invalid()
    result = {}
    for key in RESOURCE_FIELDS:
        if key in resource:
            value = resource[key]
            if not isinstance(value, str) or len(value) > 128:
                raise invalid()
            result[key] = value
    if 'amount' in resource:
        amount = resource['amount']
        if not isinstance(amount, dict):
            raise invalid()
        result['amount'] = {}
        for key in ('total', 'refund', 'currency'):
            if key not in amount:
                continue
            value = amount[key]
            if key == 'currency':
                if not isinstance(value, str) or len(value) > 3:
                    raise invalid()
            elif type(value) is not int or not 0 <= value <= 100000000000:
                raise invalid()
            result['amount'][key] = value
    return result


def receive_notification(account_key, headers, body):
    from .payments import client_for
    if (not isinstance(account_key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', account_key)
            or account_key.startswith('simulation-') or not isinstance(body, bytes)
            or not 0 < len(body) <= MAX_NOTIFICATION_BYTES):
        raise invalid()
    client = client_for(account_key)
    event = client.verify_notification(headers, body)
    event_id, event_type = event.get('id'), event.get('event_type')
    if (not isinstance(event_id, str) or not 1 <= len(event_id) <= 128 or event_type not in EVENT_TYPES
            or not isinstance(client.mchid, str) or not 1 <= len(client.mchid) <= 32
            or not isinstance(client.appid, str) or not 1 <= len(client.appid) <= 32):
        raise invalid()
    resource = minimal_resource(event.get('resource'))
    if resource.get('mchid') != client.mchid or ('appid' in resource and resource['appid'] != client.appid):
        raise invalid()
    facts = {'event_type': event_type, 'mchid': client.mchid, 'appid': client.appid, 'resource': resource}
    digest = hashlib.sha256(json.dumps(facts, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    conflict = False
    with transaction.atomic():
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('lock_timeout', '500ms', true), set_config('statement_timeout', '2000ms', true)")
        row, created = PaymentNotification.objects.get_or_create(account_key=account_key, event_id=event_id,
            defaults={**facts, 'payload_hash': digest})
        if not created and row.payload_hash != digest:
            PaymentNotification.objects.filter(pk=row.pk).update(conflict_count=F('conflict_count') + 1)
            conflict = True
    # Commit the conflict evidence even when the HTTP request must be rejected.
    if conflict:
        raise invalid('PAYMENT_NOTIFICATION_CONFLICT')
    return row


def claim_notification():
    now = timezone.now()
    eligible = (Q(status__in=('pending', 'retry'), available_at__lte=now)
        | Q(status='processing', lease_until__lte=now))
    with transaction.atomic():
        query = PaymentNotification.objects.filter(eligible).order_by('available_at', 'created_at', 'id')
        locks = {'skip_locked': True} if connection.features.has_select_for_update_skip_locked else {}
        row = query.select_for_update(**locks).first()
        if row is None:
            return None
        row.status, row.lease_token = 'processing', uuid.uuid4()
        row.lease_until = now + timedelta(seconds=60)
        row.attempts += 1
        row.save(update_fields=['status', 'lease_token', 'lease_until', 'attempts'])
        return row


class NotificationLeaseLost(Exception):
    pass


def apply_claimed_notification(row):
    from .payments import apply_notification
    try:
        with transaction.atomic():
            if connection.vendor == 'postgresql':
                with connection.cursor() as cursor:
                    cursor.execute("SELECT set_config('lock_timeout', '2000ms', true), set_config('statement_timeout', '5000ms', true)")
            # Claiming committed already. The existing helper locks Order first;
            # acquire the inbox row only AFTER applying the financial facts.
            apply_notification(row.account_key, row.mchid, row.appid, row.event_type, row.resource)
            current = PaymentNotification.objects.select_for_update().get(pk=row.pk)
            if current.status != 'processing' or current.lease_token != row.lease_token:
                raise NotificationLeaseLost()
            current.status, current.processed_at = 'done', timezone.now()
            current.lease_until = current.lease_token = None
            current.last_error_code = ''
            current.save(update_fields=['status', 'processed_at', 'lease_until', 'lease_token', 'last_error_code'])
        return True
    except NotificationLeaseLost:
        return False
    except Exception as exc:
        # Only this lease can reschedule work. No signed body or error text is logged.
        permanent = isinstance(exc, GatewayError) and not exc.retryable
        dead = permanent or row.attempts >= getattr(settings, 'PAYMENT_NOTIFICATION_MAX_ATTEMPTS', 10)
        delay = (5, 15, 30, 60, 120, 300)[min(row.attempts - 1, 5)]
        code = exc.code if isinstance(exc, GatewayError) else type(exc).__name__
        updated = PaymentNotification.objects.filter(pk=row.pk, status='processing', lease_token=row.lease_token).update(
            status='dead' if dead else 'retry', last_error_code=code[:80], lease_until=None, lease_token=None,
            available_at=timezone.now() + timedelta(seconds=delay))
        if updated:
            logging.getLogger('market').warning('payment_notification_failed notification=%s code=%s', row.pk, code[:80])
        return False


def process_notifications(*, limit=100):
    processed, failures = 0, 0
    for _ in range(max(1, min(int(limit), 1000))):
        row = claim_notification()
        if row is None:
            break
        if apply_claimed_notification(row):
            processed += 1
        else:
            failures += 1
    return processed, failures
