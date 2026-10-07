"""One bounded preview per stall; no per-stall point or review queries."""
from django.conf import settings
from datetime import timedelta
from django.db.models import Avg, Count, IntegerField, OuterRef, Prefetch, Q, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import DeliveryPoint, Product, Order, Review, Stall


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
            from .admission import new_trade_eligibility_q
            from django.db.models import F
            query = query.filter(new_trade_eligibility_q(), accepting_orders=True, location__isnull=False)
            query = query.filter(Q(current_session__stop_orders_at__isnull=True) | Q(current_session__stop_orders_at__gt=now))
            query = query.filter(Q(prep_capacity__isnull=True) | Q(prep_active_count__lt=F('prep_capacity')))
        return query
    return query.none()


def visible_stall_query(mode='detail'):
    reviews = Review.objects.filter(stall_id=OuterRef('pk')).order_by().values('stall_id')
    from .admission import public_listing_q
    query = Stall.objects.filter(public_listing_q())
    orders = Order.objects.filter(stall_id=OuterRef('pk')).order_by().values('stall_id')
    def count_orders(**filters):
        return Coalesce(Subquery(orders.filter(**filters).annotate(value=Count('pk')).values('value')[:1], output_field=IntegerField()), 0)
    query = query.select_related('area', 'merchant', 'location', 'current_session').annotate(
        prep_active_count=count_orders(status__in=('pending_payment', 'pending', 'preparing')),
        rating_average=Subquery(reviews.annotate(value=Avg('rating')).values('value')[:1]),
        rating_count=Coalesce(Subquery(reviews.annotate(value=Count('pk')).values('value')[:1], output_field=IntegerField()), 0),
    )
    if mode == 'detail':
        return query.prefetch_related(
            Prefetch('products', queryset=Product.objects.filter(is_active=True).order_by('id')),
            Prefetch('delivery_points', queryset=DeliveryPoint.objects.order_by('id'), to_attr='_delivery_points'),
            Prefetch('reviews', queryset=Review.objects.select_related('user').order_by('-created_at', '-id')[:20], to_attr='_preview_reviews'),
        ).annotate(completed_count=count_orders(status='completed'),
            delivery_active_count=count_orders(fulfillment_type='delivery',
                status__in=('pending_payment', 'pending', 'preparing', 'ready', 'delivering', 'arrived')))
    if mode == 'summary':
        from .catalogue import available_products
        query = query.prefetch_related(Prefetch('products',
            queryset=Product.objects.filter(available_products()).order_by('id')[:2],
            to_attr='_preview_products'))
    return query
