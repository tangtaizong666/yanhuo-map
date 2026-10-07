"""Bounded public discovery contracts; no complete catalog download."""
import hashlib
import json
import math
from datetime import datetime, timezone as datetime_timezone

from django.core import signing
from django.db.models import Case, DateTimeField, Exists, F, FloatField, IntegerField, OuterRef, Q, Value, When
from django.db.models.functions import ACos, Coalesce, Cos, Greatest, Least, Radians, Sin
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .discovery_queries import filter_status, visible_stall_query
from .errors import BusinessError
from .models import Follow, Product
from .serializers import PublicProductSerializer, StallMapSerializer, StallSummarySerializer


def filtered(request, context, *, mode='summary', following=False):
    query = visible_stall_query(mode)
    params = request.query_params
    if params.get('area'):
        try:
            area = int(params['area'])
            if area < 1: raise ValueError
        except (ValueError, TypeError):
            raise BusinessError('校园区域无效。', 'invalid_area', status=400) from None
        query = query.filter(area_id=area)
    term = params.get('q', '').strip()[:100]
    if term:
        matches = Product.objects.filter(stall_id=OuterRef('pk'), is_active=True).filter(Q(name__icontains=term) | Q(description__icontains=term))
        query = query.annotate(_product_match=Exists(matches)).filter(Q(name__icontains=term) | Q(description__icontains=term) | Q(_product_match=True))
    category = params.get('category')
    if category and category != 'all': query = query.filter(category=category)
    status = params.get('status', '')
    if status not in ('', 'all', 'open', 'paused', 'stale', 'closed', 'orderable'):
        raise BusinessError('营业状态筛选无效。', 'invalid_status', status=400)
    query = filter_status(query, status, context['config'])
    now = timezone.now()
    if status == 'orderable':
        query = query.filter(Q(current_session__stop_orders_at__isnull=True) | Q(current_session__stop_orders_at__gt=now)).filter(
            Q(prep_capacity__isnull=True) | Q(prep_active_count__lt=F('prep_capacity')))
    if following or params.get('follow') == '1':
        if not request.user.is_authenticated: return query.none()
        query = query.filter(pk__in=Follow.objects.filter(user=request.user).values('stall_id'))
    return query


def sorted_stalls(query, request, context):
    from datetime import timedelta
    now = timezone.now()
    ended = Q(current_session__isnull=True) | Q(current_session__status='closed') | Q(current_session__closes_at__lte=now)
    stale = Q(current_session__last_confirmed_at__isnull=True) | Q(current_session__last_confirmed_at__lt=now-timedelta(minutes=context['config'].stale_minutes))
    query = query.annotate(_status_rank=Case(When(ended, then=Value(3)), When(stale, then=Value(2)),
        When(current_session__status='paused', then=Value(1)), default=Value(0), output_field=IntegerField()),
        _confirmed=Coalesce('current_session__last_confirmed_at', Value(datetime(1970, 1, 1, tzinfo=datetime_timezone.utc)), output_field=DateTimeField()))
    sort = request.query_params.get('sort', 'freshness')
    if sort == 'distance' and context.get('point'):
        lat, lng = context['point']
        angle = Sin(Radians(Value(lat))) * Sin(Radians(F('location__latitude'))) + Cos(Radians(Value(lat))) * Cos(Radians(F('location__latitude'))) * Cos(Radians(F('location__longitude') - Value(lng)))
        distance = Case(When(location__isnull=True, then=Value(None)),
            default=Value(6371000.0) * ACos(Least(Value(1.0), Greatest(Value(-1.0), angle))), output_field=FloatField())
        query = query.annotate(distance_m_db=distance, _distance=Coalesce(distance, Value(1e20), output_field=FloatField()))
        order = ['_distance', 'id']
    elif sort in ('rating', 'popular'):
        query = query.annotate(_rating=Coalesce('rating_average', Value(0.0), output_field=FloatField()))
        order = ['-_rating', '-rating_count', 'id']
    elif sort in ('', 'recommended', 'freshness', 'distance'):
        order = ['_status_rank', '-_confirmed', 'id']
    else:
        raise BusinessError('排序方式无效。', 'invalid_sort', status=400)
    return query, order


def page(query, request, order, *, kind):
    try:
        size = int(request.query_params.get('page_size', '20'))
        if not 1 <= size <= 50: raise ValueError
    except (TypeError, ValueError):
        raise BusinessError('每页数量应为 1 至 50。', 'invalid_page_size', status=400) from None
    scope = hashlib.sha256(json.dumps([kind, request.user.pk, sorted(
        (key, value) for key, value in request.query_params.items() if key != 'cursor')], ensure_ascii=False).encode()).hexdigest()
    cursor = request.query_params.get('cursor')
    if cursor:
        try:
            if len(cursor) > 4096: raise ValueError
            record = signing.loads(cursor, salt='yanhuo.discovery.v1', max_age=3600)
            if record['scope'] != scope or len(record['values']) != len(order): raise ValueError
            clause, prefix = Q(pk__in=[]), Q()
            for field, raw in zip(order, record['values']):
                name = field.lstrip('-')
                value = parse_datetime(raw) if name == '_confirmed' else raw
                if name == '_confirmed' and value is None: raise ValueError
                clause |= prefix & Q(**{name + ('__lt' if field.startswith('-') else '__gt'): value})
                prefix &= Q(**{name: value})
            query = query.filter(clause)
        except (signing.BadSignature, ValueError, TypeError, KeyError):
            raise BusinessError('列表位置已失效，请刷新列表。', 'invalid_cursor', status=400) from None
    rows = list(query.order_by(*order)[:size + 1])
    next_cursor = None
    if len(rows) > size:
        tail = rows[size-1]
        values = [getattr(tail, field.lstrip('-')) for field in order]
        values = [value.isoformat() if isinstance(value, datetime) else value for value in values]
        next_cursor = signing.dumps({'scope': scope, 'values': values}, salt='yanhuo.discovery.v1', compress=True)
    return rows[:size], next_cursor


def stalls_response(request, context, *, following=False):
    query, order = sorted_stalls(filtered(request, context, following=following), request, context)
    rows, next_cursor = page(query, request, order, kind='follows' if following else 'stalls')
    return Response({'results': StallSummarySerializer(rows, many=True, context=context).data, 'next': next_cursor},
        headers={'Cache-Control': 'private, no-store'})


@api_view(['GET'])
@permission_classes([AllowAny])
def products(request):
    from .views import stall_context
    context = stall_context(request)
    stall_query = filtered(request, context, mode='map')
    # Restaurant discovery can include closed stalls; meal suggestions never do.
    stall_query = filter_status(stall_query, 'open', context['config'])
    from .catalogue import available_products
    query = Product.objects.filter(available_products(), stall_id__in=stall_query.values('pk'))
    term = request.query_params.get('q', '').strip()[:100]
    if term: query = query.filter(Q(name__icontains=term) | Q(description__icontains=term) | Q(stall__name__icontains=term))
    budget = request.query_params.get('budget')
    if budget:
        try:
            budget = int(budget)
            if not 1 <= budget <= 1000000: raise ValueError
        except (ValueError, TypeError):
            raise BusinessError('餐费预算无效。', 'invalid_budget', status=400) from None
        query = query.filter(price_cents__lte=budget)
    order = ['price_cents', 'id'] if request.query_params.get('meal_sort') == 'price' else ['id']
    rows, next_cursor = page(query, request, order, kind='products')
    stalls = {stall.pk: stall for stall in visible_stall_query('map').filter(pk__in={row.stall_id for row in rows})}
    for row in rows: row.stall = stalls[row.stall_id]
    data = [{'product': PublicProductSerializer(row).data,
        'stall': StallMapSerializer(stalls[row.stall_id], context=context).data} for row in rows]
    return Response({'results': data, 'next': next_cursor}, headers={'Cache-Control': 'private, no-store'})


@api_view(['GET'])
@permission_classes([AllowAny])
def map_stalls(request):
    from .views import stall_context
    context = stall_context(request)
    query = filtered(request, context, mode='map').filter(location__isnull=False)
    bounds = request.query_params.get('bounds')
    if bounds:
        try:
            west, south, east, north = map(float, bounds.split(','))
            if not all(math.isfinite(value) for value in (west, south, east, north)) or not (-180 <= west < east <= 180 and -90 <= south < north <= 90): raise ValueError
        except (ValueError, TypeError):
            raise BusinessError('地图范围无效。', 'invalid_bounds', status=400) from None
        query = query.filter(location__longitude__gte=west, location__longitude__lte=east,
            location__latitude__gte=south, location__latitude__lte=north)
    query, order = sorted_stalls(query, request, context)
    rows = list(query.order_by(*order)[:201])
    return Response({'results': StallMapSerializer(rows[:200], many=True, context=context).data,
        'truncated': len(rows) > 200, 'limit': 200}, headers={'Cache-Control': 'private, no-store'})
