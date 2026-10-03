"""Bounded order lists. Legacy callers retain the array response contract."""
import uuid

from django.core import signing
from django.db.models import Count, Exists, OuterRef, Q
from django.utils.dateparse import parse_datetime
from rest_framework.response import Response

from .errors import BusinessError
from .models import PaymentAttempt, PaymentRefund
from .serializers import OrderSerializer

ACTIVE = ('pending_payment', 'pending', 'preparing', 'ready', 'delivering', 'arrived')
UNRESOLVED_REFUNDS = ('creating', 'processing', 'reconcile', 'abnormal', 'closed')
CURSOR_SALT = 'yanhuo.orders.cursor.v1'


def scope_orders(query, *, merchant=False):
    query = query.annotate(
        _unresolved_refund=Exists(PaymentRefund.objects.filter(order_id=OuterRef('pk'),
            status__in=UNRESOLVED_REFUNDS, resolved_at__isnull=True)),
        _uncertain_payment=Exists(PaymentAttempt.objects.filter(order_id=OuterRef('pk'),
            status__in=PaymentAttempt.ACTIVE_STATUSES)),
    )
    followup = Q(payment_review_required=True) | Q(payment_status='refunding') | Q(_unresolved_refund=True) | Q(_uncertain_payment=True)
    if merchant:
        followup |= Q(fulfillment_type='delivery', status__in=('preparing', 'ready', 'delivering', 'arrived')) & ~Q(delivery_issue='')
    active = Q(status__in=ACTIVE)
    return query, {
        'all': Q(), 'active': active, 'followup': followup,
        'attention': active | followup,
        'completed': Q(status='completed'), 'cancelled': Q(status__in=('cancelled', 'rejected')),
    }


def order_list_response(query, request, *, merchant=False):
    context = {'merchant': merchant, 'request': request}
    if request.query_params.get('pagination') != 'cursor':
        return Response(OrderSerializer(query, many=True, context=context).data,
            headers={'Cache-Control': 'private, no-store'})
    selected = request.query_params.get('filter', 'all')
    query, filters = scope_orders(query, merchant=merchant)
    if selected not in filters:
        raise BusinessError('订单筛选无效。', 'invalid_filter', status=400)
    try:
        size = int(request.query_params.get('page_size', '30'))
        if not 1 <= size <= 100:
            raise ValueError
    except (ValueError, TypeError):
        raise BusinessError('每页数量应为 1 至 100。', 'invalid_page_size', status=400) from None
    scope = [request.user.pk, merchant, request.query_params.get('stall', ''), selected]
    cursor = request.query_params.get('cursor')
    position = None
    if cursor:
        try:
            if len(cursor) > 2048:
                raise ValueError
            value = signing.loads(cursor, salt=CURSOR_SALT, max_age=86400)
            created = parse_datetime(value['created'])
            key = uuid.UUID(value['id'])
            if value['scope'] != scope or created is None or created.tzinfo is None:
                raise ValueError
            position = Q(created_at__lt=created) | Q(created_at=created, pk__lt=key)
        except (signing.BadSignature, ValueError, TypeError, KeyError):
            raise BusinessError('列表位置已失效，请刷新列表。', 'invalid_cursor', status=400) from None
    counts = query.aggregate(reviewed=Count('pk', filter=Q(review__isnull=False)),
        **{name: Count('pk', filter=condition) for name, condition in filters.items()})
    selected_query = query.filter(filters[selected])
    if position is not None:
        selected_query = selected_query.filter(position)
    rows = list(selected_query.order_by('-created_at', '-pk')[:size + 1])
    next_cursor = None
    if len(rows) > size:
        tail = rows[size-1]
        next_cursor = signing.dumps({'created': tail.created_at.isoformat(), 'id': str(tail.pk), 'scope': scope},
            salt=CURSOR_SALT, compress=True)
    return Response({'results': OrderSerializer(rows[:size], many=True, context=context).data,
        'next': next_cursor, 'counts': counts}, headers={'Cache-Control': 'private, no-store'})
