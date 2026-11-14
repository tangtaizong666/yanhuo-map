"""Development-only freshness keepalive and 1-click restore for demo stalls.

Core location-expiry auto-stop (`Stall.effective_status`, `discovery_queries`,
and default `expire_orders`) remains untouched for real stalls and tests.
All helpers here are strictly gated on non-production `DEMO_MODE` and operate
exclusively on `is_demo=True, is_visible=True` stalls.
"""
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .errors import BusinessError
from .models import AuditLog, BusinessSession, SiteConfiguration, Stall

MIN_AUTO_REFRESH_STALE_MINUTES = 30
SEEDED_CLOSING_SECONDS = 6 * 3600
SEEDED_CLOSING_TOLERANCE_SECONDS = 5


def demo_environment_active() -> bool:
    return bool(settings.DEMO_MODE and not settings.PRODUCTION)


def _is_untouched_seeded_closing(
    stall_id: int,
    session: BusinessSession,
    merchant_touched_targets: set[str],
) -> bool:
    if session.closes_at is None or session.opened_at is None:
        return False
    if str(stall_id) in merchant_touched_targets:
        return False
    span = (session.closes_at - session.opened_at).total_seconds()
    return abs(span - SEEDED_CLOSING_SECONDS) <= SEEDED_CLOSING_TOLERANCE_SECONDS


def _open_demo_stall(
    stall: Stall,
    previous: BusinessSession | None,
    now,
) -> None:
    ended = (
        not previous
        or previous.status == 'closed'
        or (previous.closes_at and previous.closes_at <= now)
    )
    stall_updates = []
    if ended:
        session = BusinessSession.objects.create(
            stall=stall,
            status='open',
            opened_at=now,
            last_confirmed_at=now,
            closes_at=None,
            stop_orders_at=None,
        )
        stall.current_session = session
        stall_updates.append('current_session')
    else:
        session_updates = []
        if previous.status != 'open':
            previous.status = 'open'
            session_updates.append('status')
        if previous.last_confirmed_at != now:
            previous.last_confirmed_at = now
            session_updates.append('last_confirmed_at')
        if previous.closes_at is not None:
            previous.closes_at = None
            session_updates.append('closes_at')
        if previous.stop_orders_at is not None:
            previous.stop_orders_at = None
            session_updates.append('stop_orders_at')
        if session_updates:
            previous.save(update_fields=session_updates)
        stall.current_session = previous
    if not stall.accepting_orders:
        stall.accepting_orders = True
        stall_updates.append('accepting_orders')
    if stall_updates:
        stall.save(update_fields=stall_updates)


def _refresh_open_demo_sessions(config=None, *, lock_rows: bool = False, now=None) -> int:
    if not demo_environment_active():
        return 0
    cfg = config or SiteConfiguration.current()
    if cfg.stale_minutes < MIN_AUTO_REFRESH_STALE_MINUTES:
        return 0
    now = now or timezone.now()
    refresh_cutoff = now - timedelta(minutes=max(10, cfg.stale_minutes // 2))

    # Lock only `market_stall` rows (never join nullable `current_session` in a
    # `FOR NO KEY UPDATE` query, which PostgreSQL rejects on outer joins).
    base_qs = Stall.objects.filter(is_demo=True, is_visible=True).order_by('pk')
    if lock_rows:
        base_qs = base_qs.select_for_update(no_key=True)
    stalls = list(base_qs)
    if not stalls:
        return 0

    session_ids = [s.current_session_id for s in stalls if s.current_session_id]
    if not session_ids:
        return 0
    sessions_by_id = BusinessSession.objects.in_bulk(session_ids)
    merchant_touched_targets = set(
        AuditLog.objects.filter(
            action='stall_status',
            target__in=[str(s.pk) for s in stalls],
        ).values_list('target', flat=True)
    )

    refreshed = 0
    for stall in stalls:
        session = sessions_by_id.get(stall.current_session_id)
        if not session or session.status != 'open':
            continue
        untouched_seeded = _is_untouched_seeded_closing(
            stall.pk,
            session,
            merchant_touched_targets,
        )
        if session.closes_at and session.closes_at <= now and not untouched_seeded:
            continue

        updates: dict = {}
        if untouched_seeded:
            updates['closes_at'] = None
        if session.last_confirmed_at is None or session.last_confirmed_at < refresh_cutoff:
            updates['last_confirmed_at'] = now
        if updates:
            refreshed += BusinessSession.objects.filter(
                pk=session.pk,
                status='open',
            ).update(**updates)
    return refreshed


def keep_open_demo_stalls_fresh(config=None, *, now=None) -> int:
    """Refresh `last_confirmed_at` on already-open demo stalls in dev preview.

    Respects manual `'closed'` / `'paused'` sessions, merchant-set `closes_at`
    or `stop_orders_at` schedules, real (`is_demo=False`) stalls, and short
    `stale_minutes < 30` thresholds used when demonstrating location expiry.
    """
    return _refresh_open_demo_sessions(config, lock_rows=False, now=now)


def recover_startup_demo_stalls(*, now=None) -> int:
    """Refresh expired open demo sessions on `start-dev.ps1` startup.

    Follows the same non-intrusive rules as `keep_open_demo_stalls_fresh`: never
    reopens stalls manually paused/closed by a vendor and backs off when
    `stale_minutes < 30`, while clearing untouched `seed_demo` 6-hour defaults.
    """
    if not demo_environment_active():
        return 0
    with transaction.atomic():
        return _refresh_open_demo_sessions(lock_rows=True, now=now)


def restore_demo_stalls(*, now=None) -> dict:
    """Explicit operator action to reopen all visible demo stalls in DEMO_MODE."""
    if not demo_environment_active():
        raise BusinessError(
            '仅非生产演示环境支持一键恢复示例摊位出摊状态。',
            'demo_only',
            status=403,
        )
    now = now or timezone.now()
    with transaction.atomic():
        stalls = list(
            Stall.objects.select_for_update(no_key=True)
            .filter(is_demo=True, is_visible=True)
            .order_by('pk')
        )
        session_ids = [s.current_session_id for s in stalls if s.current_session_id]
        sessions_by_id = BusinessSession.objects.in_bulk(session_ids) if session_ids else {}
        for stall in stalls:
            _open_demo_stall(stall, sessions_by_id.get(stall.current_session_id), now)
        stall_ids = [stall.pk for stall in stalls]
    return {
        'restored_count': len(stall_ids),
        'stall_ids': stall_ids,
    }
