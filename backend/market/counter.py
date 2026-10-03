"""Small counter operations: counted stock, contact readiness and pickup lookup."""
import hashlib
import json

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.debug import sensitive_post_parameters
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle

from .errors import BusinessError
from .merchant import StrictInput
from .models import Product, StockCorrection
from .serializers import OrderSerializer, ProductSerializer
from .services import audit
from .views import merchant_stall, order_query
from .auth_limits import client_ip


class StockCorrectionInput(StrictInput):
    stock = serializers.IntegerField(min_value=0, max_value=100000)
    expected_stock_version = serializers.IntegerField(min_value=0)
    idempotency_key = serializers.CharField(min_length=8, max_length=128)
    reason = serializers.CharField(max_length=200)


def correct_stock(product_id, actor, values):
    """Shared API/admin CAS. A committed key is replayed before checking its old version."""
    form = StockCorrectionInput(data=values)
    form.is_valid(raise_exception=True)
    data = form.validated_data
    digest = hashlib.sha256(json.dumps({key: value for key, value in data.items() if key != 'idempotency_key'},
        sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with transaction.atomic():
        original = get_object_or_404(Product, pk=product_id)
        merchant_stall(original.stall_id, actor, locked=True, permission='market.change_product')
        product = Product.objects.select_for_update().get(pk=product_id)
        previous = StockCorrection.objects.filter(product=product, idempotency_key=data['idempotency_key']).first()
        if previous:
            if previous.request_hash != digest:
                raise BusinessError('更正标识已用于不同内容，请先确认原更正结果。', 'idempotency_conflict')
            return product, True
        if product.stock_version != data['expected_stock_version']:
            raise BusinessError('期间有下单、归还或补货，线上可售余量已变化，请重新盘点确认。',
                'stock_version_conflict', product=ProductSerializer(product).data)
        before, version = product.stock, product.stock_version
        product.stock, product.stock_version = data['stock'], version + 1
        product.save(update_fields=['stock', 'stock_version'])
        record = StockCorrection.objects.create(product=product, actor=actor,
            idempotency_key=data['idempotency_key'], request_hash=digest,
            stock_before=before, stock_after=product.stock, version_before=version,
            version_after=product.stock_version, reason=data['reason'])
        audit(actor, 'stock_corrected', record.pk, product_id=product.pk, stock_before=before,
            stock_after=product.stock, version_before=version, version_after=product.stock_version)
        return product, False


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def stock_correction(request, product_id):
    product, replayed = correct_stock(product_id, request.user, request.data)
    return Response({'product': ProductSerializer(product).data, 'replayed': replayed})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def receiving_heartbeat(request, stall_id):
    # Only the server can supply the timestamp. A heartbeat never confirms location
    # or asserts that the merchant has seen/accepted any particular order.
    form = StrictInput(data=request.data)
    form.is_valid(raise_exception=True)
    with transaction.atomic():
        stall = merchant_stall(stall_id, request.user, locked=True, permission='market.change_stall')
        now = timezone.now()
        if not stall.receiving_seen_at or now > stall.receiving_seen_at:
            stall.receiving_seen_at = now
            stall.save(update_fields=['receiving_seen_at'])
    return Response({'receiving_seen_at': stall.receiving_seen_at, 'receiving_status': stall.receiving_status(), 'receiving_age_seconds': 0},
        headers={'Cache-Control': 'no-store, private'})


class PickupLookupInput(StrictInput):
    pickup_code = serializers.RegexField(r'^[0-9]{8}$', max_length=8)
    number = serializers.CharField(max_length=40, required=False)


class PickupLookupThrottle(SimpleRateThrottle):
    rate = '60/minute'
    scope = 'pickup_lookup'
    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope,
            'ident': f'user:{request.user.pk}' if request.user.is_authenticated else f'ip:{client_ip(request)}'}


@sensitive_post_parameters('pickup_code')
@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([PickupLookupThrottle])
def pickup_lookup(request, stall_id):
    stall = merchant_stall(stall_id, request.user, permission='market.view_order')
    form = PickupLookupInput(data=request.data)
    form.is_valid(raise_exception=True)
    data = form.validated_data
    query = order_query().filter(stall=stall, fulfillment_type='pickup', status='ready', pickup_code=data['pickup_code'])
    if data.get('number'): query = query.filter(number=data['number'])
    matches = list(query[:2])
    if not matches:
        raise BusinessError('未找到本摊位待取餐的自取订单，请核对完整取餐码与订单状态。', 'pickup_order_not_found', status=404)
    if len(matches) > 1:
        raise BusinessError('该取餐码对应多笔待取订单，请补充完整订单号后查询。', 'pickup_code_ambiguous')
    # Deliberately omit entered codes from all audit details and merchant responses.
    audit(request.user, 'pickup_order_looked_up', matches[0].pk, stall_id=stall.pk)
    return Response(OrderSerializer(matches[0], context={'merchant': True, 'request': request}).data,
        headers={'Cache-Control': 'no-store, private'})
