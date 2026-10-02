"""Small merchant onboarding and discovery operations, with server-owned state."""
import hashlib
import json
import math

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle

from .errors import BusinessError
from .merchant import StrictInput
from .models import Area, Feedback, MerchantApplication, MerchantProfile, Order, Product, RestockBatch, Stall
from .serializers import OrderSerializer, ProductSerializer
from .services import audit
from .views import csrf, merchant_stall, order_query, visible_stalls


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


class ApplicationInput(StrictInput):
    business_name = serializers.CharField(max_length=120, allow_blank=True, required=False)
    stall_name = serializers.CharField(max_length=80, allow_blank=True, required=False)
    contact_phone = serializers.RegexField(r'^[0-9+() \-]{0,30}$', allow_blank=True, required=False)
    area_id = serializers.IntegerField(min_value=1, allow_null=True, required=False)
    category = serializers.CharField(max_length=30, allow_blank=True, required=False)
    address_note = serializers.CharField(max_length=200, allow_blank=True, required=False)
    description = serializers.CharField(max_length=1000, allow_blank=True, required=False)

    def validate_area_id(self, value):
        if value is None: return value
        choices = Area.objects.all()
        if not settings.DEMO_MODE: choices = choices.filter(is_demo=False)
        if not choices.filter(pk=value).exists():
            raise serializers.ValidationError('请选择已开放的校园区域。')
        return value


def application_data(application):
    if not application: return None
    return {key: getattr(application, key) for key in (
        'id', 'status', 'source', 'business_name', 'stall_name', 'contact_phone', 'area_id',
        'category', 'address_note', 'description', 'review_note', 'created_at', 'updated_at',
        'submitted_at', 'confirmed_at', 'approved_stall_id')}


def validate_application(application):
    required = ('business_name', 'stall_name', 'contact_phone', 'area_id', 'category', 'address_note')
    errors = {field: ['请填写此项后再提交。'] for field in required if not getattr(application, field)}
    if application.contact_phone and not 7 <= len(''.join(c for c in application.contact_phone if c.isdigit())) <= 15:
        errors['contact_phone'] = ['请填写可联系的电话号码。']
    if application.area_id and not settings.DEMO_MODE and application.area.is_demo:
        errors['area_id'] = ['请选择已开放的校园区域。']
    if errors: raise serializers.ValidationError(errors)


@api_view(['GET', 'PATCH'])
@permission_classes([IsAuthenticated])
def application(request):
    if request.method == 'GET':
        return Response({'application': application_data(MerchantApplication.objects.filter(user=request.user).first())})
    form = ApplicationInput(data=request.data)
    form.is_valid(raise_exception=True)
    with transaction.atomic():
        user = get_user_model().objects.select_for_update(no_key=True).get(pk=request.user.pk)
        existing = MerchantApplication.objects.select_for_update().filter(user=user).first()
        if existing and existing.status not in ('draft', 'needs_changes', 'rejected'):
            raise BusinessError('已提交或已批准的资料不能直接修改。', 'application_locked')
        if user.is_staff or MerchantProfile.objects.filter(user=user).exists():
            raise BusinessError('此账号已有经营身份，请联系运营维护资料。', 'already_merchant')
        value = existing or MerchantApplication(user=user)
        for key, item in form.validated_data.items(): setattr(value, key, item)
        # Every edit invalidates an earlier confirmation of these exact details.
        value.confirmed_at = None
        value.save()
        audit(user, 'merchant_application_saved', value.pk, source=value.source)
    return Response({'application': application_data(value)})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def submit_application(request):
    with transaction.atomic():
        user = get_user_model().objects.select_for_update(no_key=True).get(pk=request.user.pk)
        value = get_object_or_404(MerchantApplication.objects.select_for_update(), user=user)
        if value.status in ('submitted', 'approved'):
            return Response({'application': application_data(value)})
        if MerchantProfile.objects.filter(user=user).exists() or user.is_staff:
            raise BusinessError('此账号已有经营身份，请联系运营维护资料。', 'already_merchant')
        validate_application(value)
        value.status = 'submitted'
        value.confirmed_at = value.submitted_at = timezone.now()
        value.save(update_fields=['status', 'confirmed_at', 'submitted_at', 'updated_at'])
        audit(user, 'merchant_application_submitted', value.pk, source=value.source)
    return Response({'application': application_data(value)})


def review_application(application_id, actor, decision):
    """Admin-only approval creates a hidden, unverified shell, never trading access."""
    if not actor.is_staff or not actor.has_perm('market.change_merchantapplication'):
        raise BusinessError('没有审核权限。', 'forbidden', status=403)
    if decision not in ('approved', 'needs_changes', 'rejected'):
        raise BusinessError('审核操作无效。', status=400)
    with transaction.atomic():
        candidate = get_object_or_404(MerchantApplication, pk=application_id)
        user = get_user_model().objects.select_for_update(no_key=True).get(pk=candidate.user_id)
        value = MerchantApplication.objects.select_for_update().get(pk=application_id)
        if value.status == 'approved' and decision == 'approved': return value
        if value.status != 'submitted' or not value.confirmed_at:
            raise BusinessError('请先让申请人确认资料并提交审核。', 'application_not_submitted')
        if decision == 'approved':
            validate_application(value)
            if not user.is_active or user.is_staff or MerchantProfile.objects.filter(user=user).exists():
                raise BusinessError('账号状态或已有商户档案冲突，请先核实。', 'application_account_conflict')
            profile = MerchantProfile.objects.create(user=user, business_name=value.business_name,
                contact_phone=value.contact_phone, is_verified=False)
            value.approved_stall = Stall.objects.create(merchant=profile, area=value.area,
                name=value.stall_name, category=value.category, description=value.description,
                location_draft_address=value.address_note, is_visible=False, transaction_enabled=False,
                is_demo=value.area.is_demo, accepting_orders=True)
        elif not value.review_note.strip():
            raise BusinessError('请先填写明确的审核意见。', 'review_note_required', status=400)
        value.status, value.reviewed_by = decision, actor
        value.save()
        audit(actor, 'merchant_application_reviewed', value.pk, decision=decision,
            stall_id=value.approved_stall_id)
        return value


class RestockItem(StrictInput):
    product_id = serializers.IntegerField(min_value=1)
    quantity = serializers.IntegerField(min_value=1, max_value=100000)


class RestockInput(StrictInput):
    idempotency_key = serializers.CharField(min_length=8, max_length=128)
    items = RestockItem(many=True, min_length=1, max_length=100)
    def validate_items(self, values):
        ids = [item['product_id'] for item in values]
        if len(ids) != len(set(ids)): raise serializers.ValidationError('同一商品请合并补货数量。')
        return sorted(values, key=lambda item: item['product_id'])


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def restock(request, stall_id):
    form = RestockInput(data=request.data)
    form.is_valid(raise_exception=True)
    data = form.validated_data
    digest = fingerprint(data['items'])
    with transaction.atomic():
        # Same stall -> ordered products lock order as checkout/product editing.
        stall = merchant_stall(stall_id, request.user, locked=True)
        prior = RestockBatch.objects.filter(stall=stall, idempotency_key=data['idempotency_key']).first()
        if prior and prior.request_hash != digest:
            raise BusinessError('补货标识已用于不同内容，请重新确认。', 'idempotency_conflict')
        ids = [item['product_id'] for item in data['items']]
        rows = list(Product.objects.select_for_update().filter(stall=stall, pk__in=ids).order_by('id'))
        # A committed key stays successful even if its products were later
        # deleted. Replay only reports the surviving rows; it never adds stock.
        if not prior:
            if len(rows) != len(ids): raise BusinessError('补货商品不存在或不属于本摊位。', 'invalid_items', status=400)
            additions = {item['product_id']: item['quantity'] for item in data['items']}
            for product in rows:
                if product.stock + additions[product.pk] > 100000:
                    raise BusinessError(f'{product.name}补货后数量超出上限。', 'stock_limit', status=400)
            for product in rows:
                product.stock += additions[product.pk]
                product.stock_version += 1
                product.save(update_fields=['stock', 'stock_version'])
            batch = RestockBatch.objects.create(stall=stall, actor=request.user,
                idempotency_key=data['idempotency_key'], request_hash=digest)
            audit(request.user, 'products_restocked', batch.pk, stall_id=stall.pk, items=data['items'])
    return Response({'products': ProductSerializer(rows, many=True).data, 'replayed': bool(prior)})


class LocationSnapshot(StrictInput):
    address = serializers.CharField(max_length=200, allow_blank=True, required=False, default='')
    latitude = serializers.FloatField(min_value=-90, max_value=90, allow_null=True, required=False, default=None)
    longitude = serializers.FloatField(min_value=-180, max_value=180, allow_null=True, required=False, default=None)
    last_confirmed_at = serializers.DateTimeField(allow_null=True, required=False, default=None)
    def validate(self, attrs):
        for field in ('latitude', 'longitude'):
            if attrs[field] is not None and not math.isfinite(attrs[field]):
                raise serializers.ValidationError({field: '坐标必须是有限数值。'})
        if (attrs['latitude'] is None) != (attrs['longitude'] is None):
            raise serializers.ValidationError('经纬度应同时提供。')
        if attrs['last_confirmed_at']: attrs['last_confirmed_at'] = attrs['last_confirmed_at'].isoformat()
        return attrs


class FeedbackInput(StrictInput):
    order_id = serializers.UUIDField(required=False)
    kind = serializers.ChoiceField(choices=[item[0] for item in Feedback.KINDS], default='general')
    stall_id = serializers.IntegerField(min_value=1, required=False)
    content = serializers.CharField(max_length=1000, allow_blank=True, required=False, default='')
    contact = serializers.CharField(max_length=100, allow_blank=True, required=False, default='')
    location_snapshot = LocationSnapshot(required=False)
    idempotency_key = serializers.CharField(min_length=8, max_length=128, required=False)
    def validate(self, attrs):
        if attrs['kind'] == 'general':
            if not attrs['content']: raise serializers.ValidationError({'content': '请填写反馈内容。'})
        else:
            for field in ('stall_id', 'location_snapshot', 'idempotency_key'):
                if field not in attrs: raise serializers.ValidationError({field: '现场反馈需要此项。'})
        return attrs


class FeedbackThrottle(SimpleRateThrottle):
    scope = 'location_feedback'
    rate = '20/hour'
    def get_cache_key(self, request, view):
        ident = f'user:{request.user.pk}' if request.user.is_authenticated else f'ip:{self.get_ident(request)}'
        return self.cache_format % {'scope': self.scope, 'ident': ident}


@api_view(['POST'])
@throttle_classes([FeedbackThrottle])
def feedback(request):
    csrf(request)
    form = FeedbackInput(data=request.data)
    form.is_valid(raise_exception=True)
    data = form.validated_data
    user = request.user if request.user.is_authenticated else None
    key = data.get('idempotency_key')
    dedupe = fingerprint([user.pk if user else 'guest', key]) if key else None
    digest = fingerprint(data)
    def result(value, replayed):
        if value.request_hash != digest: raise BusinessError('此反馈标识已用于其他内容。', 'idempotency_conflict')
        return Response({'id': value.pk, 'detail': '反馈已收到，位置记录为你当时看到的信息，待运营核实。' if value.kind != 'general'
            else '反馈已提交，感谢你让校园烟火更好。', 'replayed': replayed}, status=200 if replayed else 201)
    if dedupe:
        prior = Feedback.objects.filter(dedupe_key=dedupe).first()
        if prior: return result(prior, True)
    order = None
    if data.get('order_id'):
        if user is None: raise BusinessError('请先登录后提交订单问题。', 'not_authenticated', status=403)
        order = get_object_or_404(Order.objects.select_related('stall'), pk=data['order_id'], user=user)
        if data.get('stall_id') and data['stall_id'] != order.stall_id:
            raise BusinessError('订单与摊位不一致。', 'order_stall_mismatch', status=400)
        stall = order.stall
    else:
        stall = get_object_or_404(visible_stalls(), pk=data['stall_id']) if data.get('stall_id') else None
    try:
        with transaction.atomic():
            value = Feedback.objects.create(user=user, stall=stall, order=order, kind=data['kind'], content=data['content'],
                contact=data['contact'], location_snapshot=data.get('location_snapshot', {}),
                dedupe_key=dedupe, request_hash=digest)
        return result(value, False)
    except IntegrityError:
        if not dedupe: raise
        prior = Feedback.objects.filter(dedupe_key=dedupe).first()
        if not prior: raise
        return result(prior, True)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def location_reports(request, stall_id):
    stall = merchant_stall(stall_id, request.user)
    reports = Feedback.objects.filter(stall=stall).exclude(kind='general')
    # Free text is user-controlled and can itself contain contact details. Only
    # the structured issue and the displayed location snapshot go to merchants.
    return Response({'unresolved_count': reports.filter(resolved=False).count(), 'reports': [
        {'id': row.pk, 'kind': row.kind, 'location_snapshot': row.location_snapshot,
         'snapshot_source': 'user_reported_display', 'created_at': row.created_at, 'resolved': row.resolved}
        for row in reports.order_by('resolved', '-created_at', '-id')[:20]]})


EVENT_SOURCES = ['home', 'home_recent', 'search', 'map', 'follow', 'recent_order', 'stall_detail', 'stall_qr', 'share_link', 'merchant_preview']


class EventMetadata(StrictInput):
    source = serializers.ChoiceField(choices=EVENT_SOURCES, required=False)


class EventInput(StrictInput):
    type = serializers.ChoiceField(choices=['browse', 'home_view', 'stall_view', 'map_view', 'checkout_view',
        'location_denied', 'map_error', 'api_error', 'share_click', 'share_open', 'qr_open', 'route_click', 'reorder'])
    stall_id = serializers.IntegerField(min_value=1, required=False)
    source = serializers.ChoiceField(choices=EVENT_SOURCES, required=False)
    metadata = EventMetadata(required=False, default=dict)
    def validate(self, attrs):
        if 'source' in attrs:
            existing = attrs['metadata'].get('source')
            if existing and existing != attrs['source']:
                raise serializers.ValidationError({'source': '来源信息不一致。'})
            attrs['metadata']['source'] = attrs.pop('source')
        return attrs


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def recent_completed(request):
    rows = order_query().filter(user=request.user, status='completed').order_by('-completed_at', '-created_at')[:3]
    return Response(OrderSerializer(rows, many=True).data)
