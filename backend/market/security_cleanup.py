"""Bounded housekeeping for expired login protection state."""
from datetime import timedelta

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone
from .security_models import AuthenticationFailureBucket


def cleanup_authentication_buckets(*, limit=500):
    """Delete only inactive, expired buckets; skip locks held by login attempts.

    Product creation identities are deliberately not time-expired: an old retry
    must never create another product after a housekeeping run.
    """
    now = timezone.now()
    criteria = {
        'updated_at__lt': now - timedelta(hours=24),
        'window_started_at__lte': now - timedelta(seconds=settings.AUTH_FAILURE_WINDOW_SECONDS),
    }
    with transaction.atomic():
        query = AuthenticationFailureBucket.objects.filter(**criteria).order_by('key')
        if connection.features.has_select_for_update:
            query = query.select_for_update(skip_locked=connection.features.has_select_for_update_skip_locked)
        keys = list(query.values_list('key', flat=True)[:max(1, min(limit, 1000))])
        if not keys:
            return 0
        # Recheck timestamps even on a backend without row locks.
        count, _ = AuthenticationFailureBucket.objects.filter(pk__in=keys, **criteria).delete()
        return count
