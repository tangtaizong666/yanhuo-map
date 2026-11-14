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
from .models import BusinessSession, SiteConfiguration, Stall

MIN_AUTO_REFRESH_STALE_MINUTES = 30


def demo_environment_active() -> bool:
    return bool(settings.DEMO_MODE and not settings.PRODUCTION)


def _open_demo_stall(stall: Stall, now) -> None:
    previous = stall.current_session
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
    if not stall.accepting_orders:
        stall.accepting_orders = True
        stall_updates.append('accepting_orders')
    if stall_updates:
        stall.save(update_fields=stall_updates)


def keep_open_demo_stalls_fresh(config=None, *, now=None) -> int:
    """Refresh `last_confirmed_at` on already-open demo stalls in dev preview.

    Respects manual `'closed'` / `'paused'` sessions, future `closes_at`
    schedules, real (`is_demo=False`) stalls, and short `stale_minutes < 30`
    thresholds used when demonstrating location-expiry auto-stop live.
    """
    if not demo_environment_active():
        return 0
    cfg = config or SiteConfiguration.current()
    if cfg.stale_minutes < MIN_AUTO_REFRESH_STALE_MINUTES:
        return 0
    now = now or timezone.now()
    refresh_cutoff = now - timedelta(minutes=max(10, cfg.stale_minutes // 2))
    refreshed = 0
    for stall in Stall.objects.select_related('current_session').filter(
        is_demo=True,
        is_visible=True,
        current_session__status='open',
    ):
        session = stall.current_session
        if not session:
            continue
        if session.closes_at and session.closes_at <= now:
            continue
        if session.last_confirmed_at is None or session.last_confirmed_at < refresh_cutoff:
            updated = BusinessSession.objects.filter(
                pk=session.pk,
                status='open',
            ).update(last_confirmed_at=now)
            refreshed += updated
    return refreshed


def recover_startup_demo_stalls(*, clear_seeded_closing: bool = False, now=None) -> int:
    """Ensure local dev startup never boots into an all-closed / all-stale state."""
    if not demo_environment_active():
        return 0
    now = now or timezone.now()
    cfg = SiteConfiguration.current()
    refresh_cutoff = now - timedelta(minutes=max(10, cfg.stale_minutes // 2))
    touched_ids: set[int] = set()
    with transaction.atomic():
        stalls = list(
            Stall.objects.select_for_update(no_key=True)
            .select_related('current_session')
            .filter(is_demo=True, is_visible=True)
            .order_by('pk')
        )
        if not stalls:
            return 0
        for stall in stalls:
            session = stall.current_session
            if not session or session.status != 'open':
                continue
            if session.closes_at and session.closes_at <= now:
                _open_demo_stall(stall, now)
                touched_ids.add(stall.pk)
                continue
            session_updates = []
            if session.last_confirmed_at is None or session.last_confirmed_at < refresh_cutoff:
                session.last_confirmed_at = now
                session_updates.append('last_confirmed_at')
            if clear_seeded_closing and session.closes_at is not None:
                session.closes_at = None
                session_updates.append('closes_at')
            if session.stop_orders_at and session.stop_orders_at <= now:
                session.stop_orders_at = None
                session_updates.append('stop_orders_at')
            if session_updates:
                session.save(update_fields=session_updates)
                touched_ids.add(stall.pk)

        open_count = sum(1 for stall in stalls if stall.effective_status(cfg) == 'open')
        if open_count == 0:
            for stall in stalls:
                _open_demo_stall(stall, now)
                touched_ids.add(stall.pk)
    return len(touched_ids)


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
            .select_related('current_session')
            .filter(is_demo=True, is_visible=True)
            .order_by('pk')
        )
        for stall in stalls:
            _open_demo_stall(stall, now)
        stall_ids = [stall.pk for stall in stalls]
    return {
        'restored_count': len(stall_ids),
        'stall_ids': stall_ids,
    }
