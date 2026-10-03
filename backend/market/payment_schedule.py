"""Database-owned query pacing, shared by HTTP requests and background workers.

Callers hold the order lock. Admission is separate from financial observations:
notifications update last_checked_at, but never spend or reset a query budget.
"""
from datetime import timedelta

from django.conf import settings
from django.utils import timezone


def query_due(record, now=None):
    return record.next_query_at is None or record.next_query_at <= (now or timezone.now())


def admit_query(record, now=None):
    now = now or timezone.now()
    if not query_due(record, now):
        return False
    record.next_query_at = now + timedelta(seconds=getattr(settings, 'PAYMENT_QUERY_INTERVAL_SECONDS', 5))
    record.save(update_fields=['next_query_at'])
    return True


def finish_query(record, *, failed):
    """Only the holder of the matching request lease may call this function."""
    interval = getattr(settings, 'PAYMENT_QUERY_INTERVAL_SECONDS', 5)
    if failed:
        record.consecutive_query_failures = min(record.consecutive_query_failures + 1, 1000)
        delays = getattr(settings, 'PAYMENT_QUERY_FAILURE_DELAYS', (10, 20, 30, 60))
        interval = max(interval, delays[min(record.consecutive_query_failures - 1, len(delays) - 1)])
    else:
        record.consecutive_query_failures = 0
    record.next_query_at = timezone.now() + timedelta(seconds=interval)
    record.save(update_fields=['next_query_at', 'consecutive_query_failures'])
