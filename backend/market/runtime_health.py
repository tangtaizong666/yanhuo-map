"""Read-only operational diagnostics and small, secret-free progress writes."""
import logging
from datetime import timedelta

from django.db.models import Count, F, Min, Q
from django.utils import timezone

from .operational_models import WorkerHeartbeat

logger = logging.getLogger('market.operations')
WORKERS = ('expire_orders', 'reconcile_payments', 'process_payment_notifications')


def record_worker_success(name, processed=0):
    now = timezone.now()
    WorkerHeartbeat.objects.update_or_create(name=name, defaults={
        'last_attempt_at': now, 'last_success_at': now,
        'processed': max(0, int(processed)), 'last_error_code': '',
    })


def record_worker_failure(name, exc):
    # Exception messages can contain remote payloads or credentials. Store only
    # their class, never their value or traceback in this public diagnostic path.
    now = timezone.now()
    code = type(exc).__name__[:80]
    try:
        WorkerHeartbeat.objects.get_or_create(name=name)
        WorkerHeartbeat.objects.filter(name=name).update(
            last_attempt_at=now, last_failure_at=now, last_error_code=code,
            failure_count=F('failure_count') + 1)
    except Exception:
        logger.error('worker_heartbeat_write_failed worker=%s', name)
    logger.error('worker_run_failed worker=%s code=%s', name, code)


def operations_status(*, worker=None, max_heartbeat_age=120, max_payment_age=600, max_expiry_age=120):
    from .models import Order, PaymentAttempt, PaymentRefund, PaymentNotification

    now = timezone.now()
    names = (worker,) if worker else WORKERS
    records = {row.name: row for row in WorkerHeartbeat.objects.filter(name__in=names)}
    workers = []
    for name in names:
        row = records.get(name)
        age = (now - row.last_success_at).total_seconds() if row and row.last_success_at else None
        workers.append({
            'name': name, 'healthy': age is not None and age <= max_heartbeat_age and not row.last_error_code,
            'seconds_since_success': round(age, 1) if age is not None else None,
            'processed_last_batch': row.processed if row else 0,
            'failure_count': row.failure_count if row else 0,
            'last_error_code': row.last_error_code if row else '',
        })
    result = {'status': 'ok' if all(item['healthy'] for item in workers) else 'unavailable',
              'checked_at': now.isoformat(), 'workers': workers}
    if worker:
        return result
    from .services import expiry_candidates
    # Healthy heartbeats can coexist with skipped locked rows or a stalled
    # business queue. Observe the backlog without claiming or releasing orders.
    expiry = expiry_candidates(now=now).aggregate(
        eligible_pending=Count('pk'), oldest=Min('expires_at'),
        overdue_count=Count('pk', filter=Q(expires_at__lt=now-timedelta(seconds=max_expiry_age))))
    result['order_expiry'] = {
        'eligible_pending': expiry['eligible_pending'], 'overdue_count': expiry['overdue_count'],
        'oldest_overdue_seconds': round((now-expiry['oldest']).total_seconds(), 1) if expiry['oldest'] else None,
        'overdue': bool(expiry['overdue_count']),
    }
    if result['status'] == 'ok' and result['order_expiry']['overdue']:
        result['status'] = 'attention'
    active = PaymentAttempt.objects.filter(mode='live', status__in=PaymentAttempt.ACTIVE_STATUSES)
    refunds = PaymentRefund.objects.filter(mode='live', resolved_at__isnull=True).exclude(status='success')
    oldest = min((date for date in (active.aggregate(date=Min('created_at'))['date'],
        refunds.aggregate(date=Min('created_at'))['date']) if date), default=None)
    result['payments'] = {
        'pending': active.count(), 'refunds_pending': refunds.count(),
        'reviews_required': Order.objects.filter(mode='live', payment_review_required=True).count(),
        'oldest_pending_seconds': round((now-oldest).total_seconds(), 1) if oldest else None,
        'overdue': bool(oldest and oldest < now-timedelta(seconds=max_payment_age)),
    }
    if result['status'] == 'ok' and (result['payments']['overdue'] or result['payments']['reviews_required']):
        result['status'] = 'attention'
    notifications = PaymentNotification.objects.exclude(status='done')
    pending_notifications = notifications.exclude(status='dead')
    oldest_notification = pending_notifications.aggregate(date=Min('created_at'))['date']
    result['notifications'] = {
        'pending': pending_notifications.count(), 'dead': notifications.filter(status='dead').count(),
        'conflicts': PaymentNotification.objects.filter(conflict_count__gt=0).count(),
        'oldest_pending_seconds': round((now-oldest_notification).total_seconds(), 1) if oldest_notification else None,
        'overdue': bool(oldest_notification and oldest_notification < now-timedelta(seconds=max_payment_age)),
    }
    if result['status'] == 'ok' and any(result['notifications'][key] for key in ('dead', 'conflicts', 'overdue')):
        result['status'] = 'attention'
    return result
