"""Merchant catalogue, profile, review and reporting operations.

Order transitions remain in services.py; catalogue writes use the same stall ->
product lock order as checkout so a price edit cannot restore reserved stock.
"""
import io
import hashlib
import json
import uuid
import warnings
from collections.abc import Mapping
from datetime import datetime, time, timedelta
from urllib.parse import urlsplit

from PIL import Image, ImageOps, UnidentifiedImageError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Count, Exists, F, IntegerField, OuterRef, Q, Sum, Subquery
from django.db.models.functions import TruncDate
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .errors import BusinessError
from .models import DeliveryPoint, Event, Follow, MerchantProfile, Order, OrderItem, PaymentRefund, Product, Review, Stall
from .serializers import ProductSerializer, ReviewSerializer, StallSerializer
from .services import audit
from .views import merchant_stall, stall_context
from .tastes import TasteOptionsField
from .permissions import can_operate_all, owned_or_permitted, require_stall_permission
from .security_models import ProductCreation


class StrictInput(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError({'non_field_errors': ['请提交对象格式的数据。']})
        unknown = set(data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({key: '此字段不可修改。' for key in sorted(unknown)})
        return super().to_internal_value(data)


class ImageAddress(serializers.CharField):
    def __init__(self, **kwargs):
        super().__init__(max_length=500, allow_blank=True, **kwargs)

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if value:
            try:
                parsed = urlsplit(value)
            except ValueError:
                raise serializers.ValidationError('图片地址格式不正确。')
            local = value.startswith(('/images/', '/media/')) and not parsed.netloc
            remote = parsed.scheme == 'https' and bool(parsed.netloc) and not parsed.username
            if not (local or remote) or '\\' in value:
                raise serializers.ValidationError('请上传图片，或使用 HTTPS 图片地址。')
        return value


class ProductInput(StrictInput):
    sale_paused = serializers.BooleanField(required=False, default=False)
    taste_options = TasteOptionsField(required=False, default=list)
    name = serializers.CharField(max_length=80)
    description = serializers.CharField(max_length=200, allow_blank=True, required=False, default='')
    image = ImageAddress(required=False, default='')
    category = serializers.CharField(max_length=30, allow_blank=True, required=False, default='招牌美味')
    price_cents = serializers.IntegerField(min_value=1, max_value=1000000)
    stock = serializers.IntegerField(min_value=0, max_value=100000, required=False, default=0)
    is_active = serializers.BooleanField(required=False, default=True)


class ProductCreateInput(ProductInput):
    idempotency_key = serializers.CharField(min_length=8, max_length=128, required=False)


class StallProfileInput(StrictInput):
    prep_capacity = serializers.IntegerField(min_value=1, max_value=100, allow_null=True, required=False)
    usual_hours = serializers.CharField(max_length=100, allow_blank=True, required=False)
    arrival_note = serializers.CharField(max_length=200, allow_blank=True, required=False)
    arrival_image = ImageAddress(required=False)
    location_draft_address = serializers.CharField(max_length=200, allow_blank=True, required=False)
    accepting_orders = serializers.BooleanField(required=False)
    name = serializers.CharField(max_length=80, required=False)
    description = serializers.CharField(max_length=1000, allow_blank=True, required=False)
    image = ImageAddress(required=False)
    prep_minutes = serializers.IntegerField(min_value=1, max_value=180, required=False)
    contact_phone = serializers.RegexField(r'^[0-9+() \-]{0,30}$', allow_blank=True, required=False)


class ReviewReplyInput(StrictInput):
    content = serializers.CharField(max_length=500, allow_blank=True)


class DeliveryInput(StrictInput):
    enabled = serializers.BooleanField(required=False)
    fee_cents = serializers.IntegerField(min_value=0, max_value=100000, required=False)
    min_order_cents = serializers.IntegerField(min_value=0, max_value=1000000, required=False)
    eta_min_minutes = serializers.IntegerField(min_value=5, max_value=180, required=False)
    eta_max_minutes = serializers.IntegerField(min_value=5, max_value=180, required=False)
    starts_at = serializers.TimeField(input_formats=['%H:%M'], required=False)
    ends_at = serializers.TimeField(input_formats=['%H:%M'], required=False)
    capacity = serializers.IntegerField(min_value=1, max_value=100, required=False)
    point_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), max_length=30, required=False)


def service_settings(stall):
    from .simulation import eligible
    from .serializers import public_payment_readiness
    from .delivery import delivery_settings, service_enabled
    simulated = eligible(stall)
    return {'mode': 'simulation' if simulated else 'live', 'simulation_available': simulated,
        'online_payment_enabled': stall.simulation_payment_enabled if simulated else public_payment_readiness(stall, {})['available'],
        'delivery_enabled': service_enabled(stall), 'wechat_payment': public_payment_readiness(stall, {}),
        'delivery': delivery_settings(stall, merchant=True)}


class ServicesInput(StrictInput):
    online_payment_enabled = serializers.BooleanField(required=False)
    delivery_enabled = serializers.BooleanField(required=False)


@api_view(['GET', 'PATCH'])
@permission_classes([IsAuthenticated])
def services(request, stall_id):
    if request.method == 'GET': return Response(service_settings(merchant_stall(stall_id, request.user)))
    from .simulation import eligible, prepare_stall
    form = ServicesInput(data=request.data)
    form.is_valid(raise_exception=True)
    with transaction.atomic():
        stall = merchant_stall(stall_id, request.user, locked=True, permission='market.change_stall')
        if not eligible(stall):
            raise BusinessError('快捷开关目前用于模拟经营；正式服务需由运营完成接入后启用。', 'simulation_unavailable')
        values = form.validated_data
        if any(value is True for value in values.values()) and (not stall.transaction_enabled or not stall.merchant.is_verified):
            raise BusinessError('此摊位当前没有在线接单资格，不能开启模拟交易。', 'stall_unavailable')
        if values.get('delivery_enabled') is True:
            prepare_stall(stall)
        for key, value in values.items():
            setattr(stall, 'simulation_payment_enabled' if key == 'online_payment_enabled' else 'simulation_delivery_enabled', value)
        stall.save(update_fields=['simulation_payment_enabled', 'simulation_delivery_enabled'])
        audit(request.user, 'simulation_services_updated', stall.pk, **values)
    return Response(service_settings(stall))


@api_view(['GET', 'PATCH'])
@permission_classes([IsAuthenticated])
def delivery(request, stall_id):
    from .delivery import delivery_settings, available_points
    from .simulation import eligible
    if request.method == 'GET':
        return Response(delivery_settings(merchant_stall(stall_id, request.user), merchant=True))
    form = DeliveryInput(data=request.data)
    form.is_valid(raise_exception=True)
    values = dict(form.validated_data)
    with transaction.atomic():
        stall = merchant_stall(stall_id, request.user, locked=True, permission='market.change_stall')
        point_ids = values.pop('point_ids', None)
        for key, value in values.items(): setattr(stall, 'simulation_delivery_enabled' if key == 'enabled' and eligible(stall) else 'delivery_' + key, value)
        if stall.delivery_starts_at >= stall.delivery_ends_at:
            raise BusinessError('配送时间需为同日开始时间早于结束时间的区间。', 'invalid_delivery_window', status=400)
        if stall.delivery_eta_min_minutes > stall.delivery_eta_max_minutes:
            raise BusinessError('预计送达时间下限不能大于上限。', 'invalid_delivery_eta', status=400)
        if point_ids is not None:
            points = available_points(stall).select_for_update().filter(pk__in=point_ids)
            if points.count() != len(set(point_ids)):
                raise BusinessError('只能选择当前校园由运营开放的交接点。', 'invalid_delivery_points', status=400)
            stall.delivery_points.set(points)
        if values: stall.save(update_fields=['simulation_delivery_enabled' if key == 'enabled' and eligible(stall) else 'delivery_' + key for key in values])
        audit(request.user, 'delivery_settings_updated', stall.pk, fields=list(form.validated_data))
    return Response(delivery_settings(stall, merchant=True))


def merchant_context(request):
    return {**stall_context(request), 'merchant': True}


def selected_stall(request, permission='market.view_stall'):
    value = request.query_params.get('stall')
    if not value:
        return None
    return merchant_stall(serializers.IntegerField(min_value=1).run_validation(value), request.user, permission=permission)


def owned(query, user, prefix='stall__', permission=None):
    permission = permission or f'{query.model._meta.app_label}.view_{query.model._meta.model_name}'
    return owned_or_permitted(query, user, permission, prefix + 'merchant__user')


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def stalls(request):
    # Include hidden stalls in the workspace so their owner can fix their menu.
    query = Stall.objects.select_related('area', 'merchant', 'location', 'current_session').prefetch_related('products', 'reviews__user')
    query = owned(query, request.user, prefix='')
    query = query.annotate(prep_active_count=Count('orders', filter=Q(orders__status__in=('pending_payment', 'pending', 'preparing')), distinct=True))
    return Response(StallSerializer(query.order_by('id'), many=True, context=merchant_context(request)).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def products(request, stall_id):
    form = ProductCreateInput(data=request.data)
    form.is_valid(raise_exception=True)
    values = dict(form.validated_data)
    key = values.pop('idempotency_key', None)
    digest = hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with transaction.atomic():
        stall = merchant_stall(stall_id, request.user, locked=True, permission='market.add_product')
        prior = ProductCreation.objects.filter(stall=stall, idempotency_key=key).select_related('product').first() if key else None
        if prior:
            if prior.request_hash != digest:
                raise BusinessError('此创建标识已用于不同的商品内容，请重新确认。', 'idempotency_conflict')
            if prior.product is None:
                raise BusinessError('此商品已被删除，不能重复创建。', 'idempotency_resource_gone')
            return Response(ProductSerializer(prior.product).data)
        product = Product.objects.create(stall=stall, **values)
        if key:
            ProductCreation.objects.create(stall=stall, idempotency_key=key, request_hash=digest, product=product)
        audit(request.user, 'product_created', product.id, stall_id=stall.id)
    return Response(ProductSerializer(product).data, status=201)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def product(request, product_id):
    if isinstance(request.data, Mapping) and 'stock' in request.data:
        original = get_object_or_404(Product, pk=product_id)
        merchant_stall(original.stall_id, request.user, permission='market.change_product')
        raise BusinessError('商品资料编辑不能覆盖库存，请使用补货或线上可售余量更正。', 'stock_edit_requires_correction', status=400)
    form = ProductInput(data=request.data, partial=True)
    form.is_valid(raise_exception=True)
    if not form.validated_data:
        raise BusinessError('请提供需要修改的商品信息。', status=400)
    with transaction.atomic():
        original = get_object_or_404(Product, pk=product_id)
        merchant_stall(original.stall_id, request.user, locked=True, permission='market.change_product')
        item = Product.objects.select_for_update().get(pk=product_id)
        for key, value in form.validated_data.items():
            setattr(item, key, value)
        item.save(update_fields=list(form.validated_data))
        audit(request.user, 'product_updated', item.id, fields=list(form.validated_data))
    return Response(ProductSerializer(item).data)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def profile(request, stall_id):
    form = StallProfileInput(data=request.data)
    form.is_valid(raise_exception=True)
    if not form.validated_data:
        raise BusinessError('请提供需要修改的店铺信息。', status=400)
    with transaction.atomic():
        stall = merchant_stall(stall_id, request.user, locked=True, permission='market.change_stall')
        values = dict(form.validated_data)
        if 'contact_phone' in values:
            require_stall_permission(stall, request.user, 'market.change_merchantprofile')
            # A merchant's public phone is shared by all their stalls.
            merchant = MerchantProfile.objects.select_for_update(no_key=True).get(pk=stall.merchant_id)
            merchant.contact_phone = values.pop('contact_phone')
            merchant.save(update_fields=['contact_phone'])
            stall.merchant = merchant
        for key, value in values.items():
            setattr(stall, key, value)
        if values:
            stall.save(update_fields=list(values))
        audit(request.user, 'stall_profile_updated', stall.id, fields=list(form.validated_data))
    return Response(StallSerializer(stall, context=merchant_context(request)).data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def reviews(request):
    stall = selected_stall(request, 'market.view_review')
    query = owned(Review.objects.select_related('user'), request.user)
    if stall:
        query = query.filter(stall=stall)
    return Response(ReviewSerializer(query.order_by('-created_at'), many=True).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def reply(request, review_id):
    form = ReviewReplyInput(data=request.data)
    form.is_valid(raise_exception=True)
    content = form.validated_data['content']
    with transaction.atomic():
        review = get_object_or_404(owned(Review.objects.select_for_update(), request.user, permission='market.change_review'), pk=review_id)
        review.merchant_reply, review.replied_at = content, timezone.now() if content else None
        review.save(update_fields=['merchant_reply', 'replied_at'])
        audit(request.user, 'review_replied' if content else 'review_reply_withdrawn', review.id)
    return Response(ReviewSerializer(review).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def upload_image(request, stall_id):
    stall = merchant_stall(stall_id, request.user, permission='market.change_stall')
    upload = request.FILES.get('file')
    if getattr(request._request, '_image_upload_error', None):
        raise BusinessError('图片不能超过 5 MB。', 'image_too_large', status=400)
    if not upload or not upload.size:
        raise BusinessError('请选择需要上传的图片。', 'invalid_image', status=400)
    if upload.size > 5 * 1024 * 1024:
        raise BusinessError('图片不能超过 5 MB。', 'image_too_large', status=400)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(upload, formats=('JPEG', 'PNG', 'WEBP')) as original:
                if original.width * original.height > 20_000_000:
                    raise BusinessError('图片尺寸不能超过 2000 万像素。', 'image_too_large', status=400)
                if original.format not in ('JPEG', 'PNG', 'WEBP'):
                    raise BusinessError('请选择 JPG、PNG 或 WebP 图片。', 'invalid_image', status=400)
                original.load()
                image = ImageOps.exif_transpose(original).convert('RGB')
                image.thumbnail((2000, 2000))
                buffer = io.BytesIO()
                image.save(buffer, format='JPEG', quality=88, optimize=True)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise BusinessError('图片无法读取，请重新选择有效图片。', 'invalid_image', status=400)
    path = default_storage.save(f'merchants/{stall.id}/{uuid.uuid4().hex}.jpg', ContentFile(buffer.getvalue()))
    audit(request.user, 'merchant_image_uploaded', stall.id, path=path)
    return Response({'url': default_storage.url(path)}, status=201)


def total_cents(query):
    return query.aggregate(total=Sum('total_cents'))['total'] or 0


def daily(query, field, **aggregates):
    rows = query.annotate(date=TruncDate(field)).values('date').annotate(**aggregates).order_by()
    return {row['date']: row for row in rows}


def payment_aggregates():
    return {
        'revenue_cents': Sum('total_cents', default=0),
        'offline_revenue_cents': Sum('total_cents', filter=Q(payment_method='offline'), default=0),
        'online_revenue_cents': Sum('total_cents', filter=Q(payment_method='wechat'), default=0),
        'paid_orders': Count('id'),
    }


def net_receipts(gross, refund_cents):
    return {**gross, 'refund_cents': refund_cents,
            'net_received_cents': gross.get('revenue_cents', 0) - refund_cents}


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def metrics(request):
    if not can_operate_all(request.user, 'market.view_order') and not hasattr(request.user, 'merchant_profile'):
        raise BusinessError('没有查看权限。', 'forbidden', status=403)
    stall = selected_stall(request, 'market.view_order')
    days = serializers.ChoiceField(choices=[1, 7, 30]).run_validation(request.query_params.get('days', 7))
    now = timezone.now()
    today = timezone.localdate(now)
    first_day = today - timedelta(days=days - 1)
    start = timezone.make_aware(datetime.combine(first_day, time.min))
    today_start = timezone.make_aware(datetime.combine(today, time.min))
    query = owned(Order.objects.all(), request.user)
    mode = request.query_params.get('mode', 'live')
    if mode not in ('live', 'simulation'):
        raise BusinessError('经营数据模式无效。', 'invalid_mode', status=400)
    query = query.filter(mode=mode)
    events = owned(Event.objects.all(), request.user, permission='market.view_order')
    stall_query = owned(Stall.objects.select_related('current_session'), request.user, prefix='', permission='market.view_order')
    if stall:
        query, events, stall_query = query.filter(stall=stall), events.filter(stall=stall), stall_query.filter(pk=stall.pk)
    recent = query.filter(created_at__gte=start, created_at__lte=now)
    # A later refund does not remove the original receipt from its payment day.
    received = query.filter(payment_status__in=('paid', 'refunding', 'refunded'),
                            paid_at__gte=start, paid_at__lte=now)
    refunded = PaymentRefund.objects.filter(order__in=query, status='success',
                                           completed_at__gte=start, completed_at__lte=now)
    fulfilled = query.filter(completed_at__gte=start, completed_at__lte=now, status='completed')
    # A returning customer has an earlier completed order at the same stall.
    # This measures repeat fulfilment, not unique visitors or a retention cohort.
    earlier = Order.objects.filter(stall_id=OuterRef('stall_id'), user_id=OuterRef('user_id'),
        mode=mode, status='completed', completed_at__lt=OuterRef('completed_at'))
    completed_customers = fulfilled.values('user_id').distinct().count()
    returning_customers = fulfilled.annotate(has_earlier=Exists(earlier)).filter(has_earlier=True).values('user_id').distinct().count()
    recent_events = events.filter(created_at__gte=start, created_at__lte=now)
    from .operations import EVENT_SOURCES
    source_rows = recent_events.filter(metadata__source__in=EVENT_SOURCES).values('metadata__source', 'type').annotate(
        event_count=Count('id')).order_by('-event_count', 'metadata__source', 'type')
    source_counts = [{'source': row['metadata__source'], 'event_type': row['type'], 'count': row['event_count']}
        for row in source_rows]
    views, total, completed = recent_events.filter(type='stall_view').count(), recent.count(), recent.filter(status='completed').count()
    gross = received.aggregate(**payment_aggregates())
    paid_count, revenue = gross['paid_orders'], gross['revenue_cents']
    refund_cents = refunded.aggregate(cents=Sum('amount_cents', default=0))['cents']
    created_daily = daily(recent, 'created_at', count=Count('id'))
    completed_daily = daily(fulfilled, 'completed_at', count=Count('id'))
    paid_daily = daily(received, 'paid_at', **payment_aggregates())
    refund_daily = daily(refunded, 'completed_at', cents=Sum('amount_cents', default=0))
    empty_gross = {name: 0 for name in payment_aggregates()}
    series = []
    for offset in range(days):
        day = first_day + timedelta(days=offset)
        day_gross = {name: paid_daily.get(day, {}).get(name, 0) for name in empty_gross}
        series.append({'date': day.isoformat(), 'orders_created': created_daily.get(day, {}).get('count', 0),
            'orders_completed': completed_daily.get(day, {}).get('count', 0),
            **net_receipts(day_gross, refund_daily.get(day, {}).get('cents', 0))})
    products = Product.objects.filter(stall__in=stall_query)
    today_paid = received.filter(paid_at__gte=today_start)
    today_gross = today_paid.aggregate(**payment_aggregates())
    today_refunded = refunded.filter(completed_at__gte=today_start).aggregate(cents=Sum('amount_cents', default=0))['cents']
    top = OrderItem.objects.filter(order__in=received).values('name').annotate(
        revenue_cents=Sum(F('unit_price_cents') * F('quantity'), output_field=IntegerField()),
        quantity=Sum('quantity')).order_by('-quantity', '-revenue_cents', 'name')[:5]
    latest_refund = PaymentRefund.objects.filter(order_id=OuterRef('pk')).order_by('-created_at', '-pk')
    payments = list(received.order_by('-paid_at').values('id', 'number', 'total_cents', 'paid_at', 'stall_name',
        'payment_method', 'payment_status', refund_status=Subquery(latest_refund.values('status')[:1]),
        refunded_at=Subquery(latest_refund.values('completed_at')[:1]))[:20])
    for payment in payments:
        payment['refunded'] = payment['refund_status'] == 'success'
    return Response({'mode': mode, 'period_days': days, 'stall_views': views, 'orders_created': total, 'orders_completed': completed,
        'source_counts': source_counts,
        'completed_customer_count': completed_customers, 'returning_customer_count': returning_customers,
        'returning_customer_rate': round(returning_customers / completed_customers, 3) if completed_customers else None,
        'view_to_order_rate': round(total / views, 3) if views else None,
        'order_completion_rate': round(completed / total, 3) if total else None,
        'pending_orders': query.filter(status='pending').count(),
        'uncollected_orders': query.filter(Q(fulfillment_type='pickup', status='ready', ready_at__lt=now - timedelta(hours=1)) | Q(fulfillment_type='delivery', status='arrived', arrived_at__lt=now - timedelta(hours=1))).count(),
        'expired_orders': recent_events.filter(type='order_expired').count(),
        'stale_stalls': sum(s.effective_status() == 'stale' for s in stall_query),
        **net_receipts(gross, refund_cents),
        'average_order_cents': round(revenue / paid_count) if paid_count else 0,
        'followers': Follow.objects.filter(stall__in=stall_query).count(),
        'products_active': products.filter(is_active=True).count(),
        'products_sold_out': products.filter(is_active=True, stock=0).count(),
        'today': {**net_receipts(today_gross, today_refunded),
            'orders_created': recent.filter(created_at__gte=today_start).count(),
            'orders_completed': fulfilled.filter(completed_at__gte=today_start).count()},
        'series': series, 'top_products': list(top), 'recent_payments': payments,
        'api_errors': recent_events.filter(type='api_error').count(),
        'metric_definitions': {
            'period': '按 Asia/Shanghai 自然日统计，包含今天。',
            'mode': '订单、收款、退款、商品销量按不可变订单模式区分，模拟金额不计入正式收款。浏览与接口事件属于摊位共享访问，因此转化率仅供页面流程参考，不代表真实运营转化。',
            'revenue': '线下确认收款与微信核实收款的总额，按 paid_at 统计；后续退款不改写历史收款，不代表平台余额或可提现金额。',
            'payment_methods': 'offline_revenue_cents 为到摊付款，online_revenue_cents 为微信支付，均按 paid_at 统计。',
            'refunds': '仅统计已确认退款成功的金额，按退款 completed_at 统计，不按下单时间或退款申请时间统计。',
            'net_received': '当期总收款减当期成功退款；退款可能来自更早订单，净收款允许为负，不代表可提现余额。',
            'orders_completed': '所选期间创建的订单中，当前已完成的订单数。',
            'series_completed': '每日实际完成自取或收餐的订单数，按 completed_at 统计。',
            'conversion': '下单数 / 摊位详情浏览事件数；完成率为所选期间创建订单中已完成的比例。浏览事件未按用户去重。',
            'top_products': '所选期间已收款订单的商品快照，按名称汇总销量与金额。',
            'returning_customers': '本期完成订单的用户中，在该次完成前曾于同一摊位完成同模式订单的用户数；按用户去重。完成后退款不抹去实际履约历史，不代表留存率或净成交人数。',
            'source_counts': '摊位与期间内客户端声明的白名单入口事件次数，未按用户去重；访问事件由模拟与正式共享，不能解释为真实扫码人数、订单归因或转化率。没有来源的旧事件不计入此表。',
        }})
