"""One bounded preview per stall; no per-stall point or review queries."""
from django.conf import settings
from datetime import timedelta
from django.db.models import Avg, Count, IntegerField, OuterRef, Prefetch, Q, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import DeliveryPoint, Review, Stall


def filter_status(query, status, config):
    """Filter before prefetching menus/reviews for stalls that won't be returned."""
    if not status or status == 'all': return query
    now = timezone.now()
    ended = Q(current_session__isnull=True) | Q(current_session__status='closed') | Q(current_session__closes_at__lte=now)
    stale = Q(current_session__last_confirmed_at__isnull=True) | Q(current_session__last_confirmed_at__lt=now-timedelta(minutes=config.stale_minutes))
    if status == 'closed': return query.filter(ended)
    if status == 'stale': return query.exclude(ended).filter(stale)
    if status in ('open', 'paused', 'orderable'):
        query = query.exclude(ended).exclude(stale).filter(current_session__status='open' if status == 'orderable' else status)
        if status == 'orderable':
            query = query.filter(accepting_orders=True, transaction_enabled=True, merchant__is_verified=True, location__isnull=False)
        return query
    return query.none()


def visible_stall_query():
    reviews = Review.objects.filter(stall_id=OuterRef('pk')).order_by().values('stall_id')
    query = Stall.objects.filter(is_visible=True)
    if not settings.DEMO_MODE:
        query = query.filter(is_demo=False)
    return query.select_related('area', 'merchant', 'location', 'current_session').prefetch_related(
        'products',
        Prefetch('delivery_points', queryset=DeliveryPoint.objects.order_by('id'), to_attr='_delivery_points'),
        Prefetch('reviews', queryset=Review.objects.select_related('user').order_by('-created_at', '-id')[:20], to_attr='_preview_reviews'),
    ).annotate(
        completed_count=Count('orders', filter=Q(orders__status='completed'), distinct=True),
        prep_active_count=Count('orders', filter=Q(orders__status__in=('pending_payment', 'pending', 'preparing')), distinct=True),
        delivery_active_count=Count('orders', filter=Q(orders__fulfillment_type='delivery',
            orders__status__in=('pending_payment', 'pending', 'preparing', 'ready', 'delivering', 'arrived')), distinct=True),
        rating_average=Subquery(reviews.annotate(value=Avg('rating')).values('value')[:1]),
        rating_count=Coalesce(Subquery(reviews.annotate(value=Count('pk')).values('value')[:1], output_field=IntegerField()), 0),
    )
