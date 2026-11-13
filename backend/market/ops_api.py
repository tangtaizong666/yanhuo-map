"""Platform operator (administrator) governance API.

All endpoints require an active staff account plus explicit Django model/custom
permissions via ``can_operate_all`` / ``require_operator``. Order fulfilment
transitions remain strictly owned by merchants and students; operators only
inspect orders, run provider-verified financial resolution, export
reconciliation CSVs, verify onboarding/licences/feedback, and manage campus
areas, delivery handoff points, and global thresholds.
"""
import math
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .admission import (
    capabilities,
    has_admission_text,
    merchant_trade_reason,
    new_trade_eligible,
    pickup_eligibility_reason,
)
from .auth_limits import TrustedUserRateThrottle
from .errors import BusinessError
from .financial_export import reconciliation_export
from .list_api import CURSOR_SALT, scope_orders
from .merchant import StrictInput
from .models import (
    OPEN_ORDER_STATUSES,
    Area,
    AuditLog,
    DeliveryPoint,
    Feedback,
    MerchantApplication,
    MerchantProfile,
    Order,
    PaymentAttempt,
    PaymentRefund,
    SiteConfiguration,
    Stall,
    StallLocation,
)
from .operations import application_data, review_application
from .permissions import can_operate_all
from .pilot_metrics import evidence as compute_pilot_evidence
from .reconciliation import (
    confirm_uncreated_payment,
    require_operator,
    request_operator_refund,
    verify_and_resolve,
    verify_external_refund,
)
from .runtime_health import operations_status
from .serializers import OrderSerializer
from .services import audit
from .views import order_query

OPS_CURSOR_SALT = CURSOR_SALT + '.ops'


def require_staff(user):
    if not (user and user.is_authenticated and user.is_active and user.is_staff):
        raise BusinessError('仅平台管理员可访问管理后台。', 'forbidden', status=403)


def require_ops_perm(user, *permissions, any_of=False):
    require_staff(user)
    if user.is_superuser:
        return
    checks = (can_operate_all(user, perm) for perm in permissions)
    allowed = any(checks) if any_of else all(checks)
    if not allowed:
        raise BusinessError('当前管理员账号没有执行此操作的权限。', 'forbidden', status=403)


def ops_permissions_map(user):
    if not (user and user.is_authenticated and user.is_active and user.is_staff):
        return {}
    has = lambda perm: can_operate_all(user, perm)
    return {
        'manage_config': has('market.change_siteconfiguration'),
        'view_applications': has('market.view_merchantapplication') or has('market.change_merchantapplication'),
        'review_applications': has('market.change_merchantapplication'),
        'create_assisted_application': has('market.add_merchantapplication') and has('market.change_merchantapplication'),
        'view_merchants': (
            has('market.view_stall')
            or has('market.change_stall')
            or has('market.view_merchantprofile')
            or has('market.change_merchantprofile')
        ),
        'view_merchant_profiles': has('market.view_merchantprofile') or has('market.change_merchantprofile'),
        'manage_merchants': has('market.change_merchantprofile'),
        'manage_stalls': has('market.change_stall'),
        'view_areas': (
            has('market.view_area')
            or has('market.change_area')
            or has('market.view_deliverypoint')
            or has('market.change_deliverypoint')
        ),
        'add_areas': has('market.add_area'),
        'manage_areas': has('market.change_area'),
        'add_delivery_points': has('market.add_deliverypoint'),
        'manage_delivery_points': has('market.change_deliverypoint'),
        'view_orders': has('market.view_order'),
        'resolve_payments': has('market.resolve_payments'),
        'export_reconciliation': has('market.export_financial_reconciliation'),
        'view_feedback': has('market.view_feedback') or has('market.change_feedback'),
        'manage_feedback': has('market.change_feedback'),
        'view_users': has('auth.view_user') or has('auth.change_user'),
        'manage_users': has('auth.change_user'),
        'view_audit': has('market.view_auditlog'),
    }


def _raise_django_validation(exc: DjangoValidationError):
    if hasattr(exc, 'message_dict'):
        raise serializers.ValidationError(exc.message_dict)
    raise serializers.ValidationError({'non_field_errors': list(exc.messages)})


class ConfigUpdateInput(StrictInput):
    stale_minutes = serializers.IntegerField(min_value=1, max_value=1440, required=False)
    pending_minutes = serializers.IntegerField(min_value=1, max_value=60, required=False)


class AssistedApplicationInput(StrictInput):
    username = serializers.CharField(max_length=150)
    business_name = serializers.CharField(max_length=120, allow_blank=True, required=False, default='')
    stall_name = serializers.CharField(max_length=80, allow_blank=True, required=False, default='')
    contact_phone = serializers.RegexField(r'^[0-9+() \-]{0,30}$', allow_blank=True, required=False, default='')
    area_id = serializers.IntegerField(min_value=1, allow_null=True, required=False, default=None)
    category = serializers.CharField(max_length=30, allow_blank=True, required=False, default='')
    address_note = serializers.CharField(max_length=200, allow_blank=True, required=False, default='')
    description = serializers.CharField(max_length=1000, allow_blank=True, required=False, default='')

    def validate_area_id(self, value):
        if value is None:
            return value
        choices = Area.objects.all()
        if not settings.DEMO_MODE:
            choices = choices.filter(is_demo=False)
        if not choices.filter(pk=value).exists():
            raise serializers.ValidationError('请选择已开放的校园区域。')
        return value


class ApplicationReviewInput(StrictInput):
    decision = serializers.ChoiceField(choices=['approved', 'needs_changes', 'rejected'])
    review_note = serializers.CharField(max_length=1000, allow_blank=True, required=False)


class MerchantUpdateInput(StrictInput):
    business_name = serializers.CharField(max_length=120, required=False)
    contact_phone = serializers.RegexField(r'^[0-9+() \-]{0,30}$', allow_blank=True, required=False)
    qualification_note = serializers.CharField(max_length=2000, allow_blank=True, required=False)
    qualification_tier = serializers.ChoiceField(choices=['mobile_vendor', 'storefront'], required=False)
    is_verified = serializers.BooleanField(required=False)
    license_number = serializers.CharField(max_length=100, allow_blank=True, required=False)
    licensed_business_address = serializers.CharField(max_length=200, allow_blank=True, required=False)
    food_preparation_address = serializers.CharField(max_length=200, allow_blank=True, required=False)
    license_valid_until = serializers.DateField(allow_null=True, required=False)
    wechat_pay_account = serializers.CharField(max_length=64, allow_blank=True, allow_null=True, required=False)


class StallLocationInput(StrictInput):
    address = serializers.CharField(max_length=200)
    latitude = serializers.FloatField(min_value=-90, max_value=90)
    longitude = serializers.FloatField(min_value=-180, max_value=180)

    def validate(self, attrs):
        if not has_admission_text(attrs['address']):
            raise serializers.ValidationError({'address': '请填写具体的取餐位置说明。'})
        for key in ('latitude', 'longitude'):
            if not math.isfinite(attrs[key]):
                raise serializers.ValidationError({key: '请输入有限经纬度数值。'})
        return attrs


class StallGovernanceInput(StrictInput):
    name = serializers.CharField(max_length=80, required=False)
    category = serializers.CharField(max_length=30, required=False)
    area_id = serializers.IntegerField(min_value=1, required=False)
    is_visible = serializers.BooleanField(required=False)
    public_phone_enabled = serializers.BooleanField(required=False)
    transaction_enabled = serializers.BooleanField(required=False)
    delivery_approved = serializers.BooleanField(required=False)
    delivery_enabled = serializers.BooleanField(required=False)
    delivery_point_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1), max_length=30, required=False
    )
    location = StallLocationInput(required=False)

    def validate_name(self, value):
        if not has_admission_text(value):
            raise serializers.ValidationError('摊位名称不能为空。')
        return value.strip()


class AreaWriteInput(StrictInput):
    name = serializers.CharField(max_length=80, required=False)
    subtitle = serializers.CharField(max_length=160, allow_blank=True, required=False)
    latitude = serializers.FloatField(min_value=-90, max_value=90, required=False)
    longitude = serializers.FloatField(min_value=-180, max_value=180, required=False)
    is_demo = serializers.BooleanField(required=False)

    def validate(self, attrs):
        if 'name' in attrs and not has_admission_text(attrs['name']):
            raise serializers.ValidationError({'name': '区域名称不能为空。'})
        for key in ('latitude', 'longitude'):
            if key in attrs and not math.isfinite(attrs[key]):
                raise serializers.ValidationError({key: '请输入有限经纬度数值。'})
        if attrs.get('is_demo') and not settings.DEMO_MODE:
            raise serializers.ValidationError({'is_demo': '非演示环境不能创建或启用示例区域。'})
        return attrs


class DeliveryPointWriteInput(StrictInput):
    area_id = serializers.IntegerField(min_value=1, required=False)
    name = serializers.CharField(max_length=80, required=False)
    address = serializers.CharField(max_length=200, required=False)
    latitude = serializers.FloatField(min_value=-90, max_value=90, required=False)
    longitude = serializers.FloatField(min_value=-180, max_value=180, required=False)
    is_active = serializers.BooleanField(required=False)

    def validate(self, attrs):
        for key, label in (('name', '交接点名称'), ('address', '交接位置说明')):
            if key in attrs and not has_admission_text(attrs[key]):
                raise serializers.ValidationError({key: f'{label}不能为空。'})
        for key in ('latitude', 'longitude'):
            if key in attrs and not math.isfinite(attrs[key]):
                raise serializers.ValidationError({key: '请输入有限经纬度数值。'})
        return attrs


class FeedbackReviewInput(StrictInput):
    verification = serializers.ChoiceField(choices=['unreviewed', 'confirmed', 'dismissed'], required=False)
    verification_note = serializers.CharField(max_length=500, allow_blank=True, required=False)
    resolved = serializers.BooleanField(required=False)


class FinancialResolutionInput(StrictInput):
    operation = serializers.ChoiceField(
        choices=['verify', 'uncreated', 'external', 'retry', 'compensation']
    )
    payment_id = serializers.UUIDField(required=False, allow_null=True)
    refund_id = serializers.UUIDField(required=False, allow_null=True)
    out_refund_no = serializers.CharField(max_length=64, allow_blank=True, required=False, default='')
    expected_amount_cents = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    reason = serializers.CharField(max_length=200)
    confirm_refund = serializers.BooleanField(required=False, default=False)
    operation_key = serializers.CharField(min_length=8, max_length=128, required=False, default='')

    def validate(self, attrs):
        op = attrs['operation']
        if op != 'verify' and not attrs.get('payment_id'):
            raise serializers.ValidationError({'payment_id': '请选择要核验的原付款记录。'})
        if op == 'external' and not attrs.get('out_refund_no', '').strip():
            raise serializers.ValidationError({'out_refund_no': '请填写外部商户退款单号。'})
        if op == 'retry' and not attrs.get('refund_id'):
            raise serializers.ValidationError({'refund_id': '请选择原关闭退款记录。'})
        if op in ('retry', 'compensation'):
            if not attrs.get('confirm_refund'):
                raise serializers.ValidationError({'confirm_refund': '发起渠道退款须勾选确认原付款与金额。'})
            if not attrs.get('expected_amount_cents'):
                raise serializers.ValidationError({'expected_amount_cents': '请填写确认退款金额（分）。'})
            if not attrs.get('operation_key'):
                raise serializers.ValidationError({'operation_key': '缺少幂等操作标识。'})
        return attrs


class UserGovernanceInput(StrictInput):
    is_active = serializers.BooleanField(required=False)
    display_name = serializers.CharField(max_length=30, allow_blank=True, required=False)


def _can_view_merchant_sensitive(user) -> bool:
    return bool(
        user.is_superuser
        or can_operate_all(user, 'market.view_merchantprofile')
        or can_operate_all(user, 'market.change_merchantprofile')
    )


def _serialize_merchant(merchant: MerchantProfile, *, include_sensitive: bool = True):
    return {
        'id': merchant.pk,
        'user_id': merchant.user_id,
        'username': merchant.user.username,
        'display_name': merchant.user.first_name or merchant.user.username,
        'business_name': merchant.business_name,
        'contact_phone': merchant.contact_phone if include_sensitive else '',
        'qualification_note': merchant.qualification_note if include_sensitive else '',
        'qualification_tier': merchant.qualification_tier,
        'is_verified': merchant.is_verified,
        'license_number': merchant.license_number if include_sensitive else '',
        'licensed_business_address': merchant.licensed_business_address if include_sensitive else '',
        'food_preparation_address': merchant.food_preparation_address if include_sensitive else '',
        'license_valid_until': merchant.license_valid_until.isoformat() if merchant.license_valid_until else None,
        'wechat_pay_account': (merchant.wechat_pay_account or '') if include_sensitive else '',
        'trade_reason': merchant_trade_reason(merchant),
        'sensitive_redacted': not include_sensitive,
    }


def _serialize_stall_governance(
    stall: Stall,
    cfg: SiteConfiguration,
    *,
    include_merchant_sensitive: bool = True,
):
    loc = getattr(stall, 'location', None)
    session = stall.current_session
    caps = capabilities(stall, {'config': cfg})
    return {
        'id': stall.pk,
        'merchant_id': stall.merchant_id,
        'merchant': _serialize_merchant(stall.merchant, include_sensitive=include_merchant_sensitive),
        'area_id': stall.area_id,
        'area_name': stall.area.name,
        'name': stall.name,
        'category': stall.category,
        'description': stall.description,
        'is_demo': stall.is_demo,
        'is_visible': stall.is_visible,
        'public_phone_enabled': stall.public_phone_enabled,
        'accepting_orders': stall.accepting_orders,
        'transaction_enabled': stall.transaction_enabled,
        'transaction_eligible': new_trade_eligible(stall),
        'delivery_approved': stall.delivery_approved,
        'delivery_enabled': stall.delivery_enabled,
        'status': stall.effective_status(cfg),
        'last_confirmed_at': session.last_confirmed_at.isoformat() if session and session.last_confirmed_at else None,
        'location_draft_address': stall.location_draft_address,
        'location': (
            {
                'address': loc.address,
                'latitude': loc.latitude,
                'longitude': loc.longitude,
                'updated_at': loc.updated_at.isoformat() if loc.updated_at else None,
            }
            if loc is not None
            else None
        ),
        'delivery_point_ids': [p.pk for p in stall.delivery_points.all()],
        'product_count': len(list(stall.products.all())),
        'active_product_count': sum(1 for p in stall.products.all() if p.is_active),
        'capabilities': caps,
    }


def _serialize_application(app: MerchantApplication):
    base = application_data(app)
    return {
        **base,
        'username': app.user.username,
        'display_name': app.user.first_name or app.user.username,
        'area_name': app.area.name if app.area_id and app.area else '',
        'reviewed_by_username': app.reviewed_by.username if app.reviewed_by_id and app.reviewed_by else '',
    }


@api_view(['GET', 'PATCH'])
@permission_classes([IsAuthenticated])
@throttle_classes([TrustedUserRateThrottle])
def overview(request):
    require_staff(request.user)
    if request.method == 'PATCH':
        require_ops_perm(request.user, 'market.change_siteconfiguration')
        form = ConfigUpdateInput(data=request.data)
        form.is_valid(raise_exception=True)
        data = form.validated_data
        with transaction.atomic():
            cfg = SiteConfiguration.objects.select_for_update().filter(pk=1).first() or SiteConfiguration(pk=1)
            changed = []
            for key in ('stale_minutes', 'pending_minutes'):
                if key in data and getattr(cfg, key) != data[key]:
                    setattr(cfg, key, data[key])
                    changed.append(key)
            cfg.save()
            if changed:
                audit(
                    request.user,
                    'ops_site_config_updated',
                    'market.SiteConfiguration:1',
                    fields=changed,
                    stale_minutes=cfg.stale_minutes,
                    pending_minutes=cfg.pending_minutes,
                )
        return Response({'stale_minutes': cfg.stale_minutes, 'pending_minutes': cfg.pending_minutes})

    cfg = SiteConfiguration.current()
    perms = ops_permissions_map(request.user)
    try:
        days = int(request.query_params.get('days', '7'))
        if days not in (1, 7, 30):
            raise ValueError
    except (TypeError, ValueError):
        raise BusinessError('统计天数仅支持 1、7 或 30 天。', 'invalid_days', status=400) from None

    default_mode = (
        'simulation'
        if settings.DEMO_MODE and not Order.objects.filter(mode='live').exists()
        else 'live'
    )
    mode = request.query_params.get('mode', default_mode)
    if mode not in ('live', 'simulation'):
        raise BusinessError('统计模式无效。', 'invalid_mode', status=400)

    all_stalls = list(
        Stall.objects.select_related('merchant', 'current_session', 'location').all()
    )
    open_count = 0
    stale_count = 0
    trade_eligible_count = 0
    visible_count = 0
    for stall in all_stalls:
        eff = stall.effective_status(cfg)
        if eff == 'open':
            open_count += 1
        elif eff == 'stale':
            stale_count += 1
        if stall.is_visible:
            visible_count += 1
        if new_trade_eligible(stall):
            trade_eligible_count += 1

    User = get_user_model()
    live_orders, live_filters = scope_orders(Order.objects.filter(mode='live'), merchant=True)
    sim_orders, sim_filters = scope_orders(Order.objects.filter(mode='simulation'), merchant=True)

    now = timezone.now()
    start = now - timedelta(days=days)
    pilot = compute_pilot_evidence(
        Order.objects.filter(mode=mode),
        Stall.objects.all(),
        start,
        now,
        mode,
    )

    return Response(
        {
            'permissions': perms,
            'config': {
                'stale_minutes': cfg.stale_minutes,
                'pending_minutes': cfg.pending_minutes,
            },
            'runtime': operations_status(),
            'summary': {
                'stalls_total': len(all_stalls),
                'stalls_visible': visible_count,
                'stalls_open': open_count,
                'stalls_stale': stale_count,
                'stalls_trade_eligible': trade_eligible_count,
                'applications_submitted': MerchantApplication.objects.filter(status='submitted').count(),
                'applications_needs_changes': MerchantApplication.objects.filter(status='needs_changes').count(),
                'applications_draft': MerchantApplication.objects.filter(status='draft').count(),
                'feedback_unresolved': Feedback.objects.filter(resolved=False).count(),
                'feedback_unreviewed_location': Feedback.objects.exclude(kind='general').filter(verification='unreviewed').count(),
                'orders_live_active': live_orders.filter(live_filters['active']).count(),
                'orders_live_attention': live_orders.filter(live_filters['attention']).count(),
                'orders_simulation_active': sim_orders.filter(sim_filters['active']).count(),
                'orders_simulation_attention': sim_orders.filter(sim_filters['attention']).count(),
                'merchants_total': MerchantProfile.objects.count(),
                'merchants_verified': MerchantProfile.objects.filter(is_verified=True).count(),
                'users_total': User.objects.count(),
                'staff_total': User.objects.filter(is_staff=True).count(),
            },
            'pilot': {**pilot, 'days': days},
        },
        headers={'Cache-Control': 'private, no-store'},
    )


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([TrustedUserRateThrottle])
def applications(request):
    if request.method == 'GET':
        require_ops_perm(
            request.user,
            'market.view_merchantapplication',
            'market.change_merchantapplication',
            any_of=True,
        )
        base = MerchantApplication.objects.select_related('user', 'area', 'reviewed_by')
        counts = base.aggregate(
            all=Count('pk'),
            submitted=Count('pk', filter=Q(status='submitted')),
            needs_changes=Count('pk', filter=Q(status='needs_changes')),
            draft=Count('pk', filter=Q(status='draft')),
            approved=Count('pk', filter=Q(status='approved')),
            rejected=Count('pk', filter=Q(status='rejected')),
        )
        status_filter = request.query_params.get('status', 'all')
        if status_filter != 'all':
            if status_filter not in ('submitted', 'needs_changes', 'draft', 'approved', 'rejected'):
                raise BusinessError('入驻申请筛选状态无效。', 'invalid_status', status=400)
            base = base.filter(status=status_filter)
        q = request.query_params.get('q', '').strip()
        if q:
            base = base.filter(
                Q(stall_name__icontains=q)
                | Q(business_name__icontains=q)
                | Q(user__username__icontains=q)
                | Q(user__first_name__icontains=q)
            )
        rows = list(base.order_by('-updated_at', '-pk')[:100])
        rows.sort(key=lambda item: (0 if item.status == 'submitted' else 1 if item.status == 'needs_changes' else 2))
        return Response(
            {
                'counts': counts,
                'results': [_serialize_application(row) for row in rows],
            },
            headers={'Cache-Control': 'private, no-store'},
        )

    require_ops_perm(request.user, 'market.add_merchantapplication', 'market.change_merchantapplication')
    form = AssistedApplicationInput(data=request.data)
    form.is_valid(raise_exception=True)
    data = form.validated_data
    User = get_user_model()
    with transaction.atomic():
        target = User.objects.select_for_update(no_key=True).filter(username=data['username'].strip()).first()
        if target is None:
            raise BusinessError('未找到该注册用户名，请先让申请人注册普通账号。', 'user_not_found', status=404)
        if not target.is_active or target.is_staff or MerchantProfile.objects.filter(user=target).exists():
            raise BusinessError('请选择已启用、非管理员且尚无商户档案的普通账号。', 'invalid_applicant', status=400)
        if MerchantApplication.objects.filter(user=target).exists():
            raise BusinessError('该账号已有入驻申请记录，请直接在申请列表中查看。', 'application_exists', status=409)
        created = MerchantApplication.objects.create(
            user=target,
            source='assisted',
            status='draft',
            business_name=data['business_name'],
            stall_name=data['stall_name'],
            contact_phone=data['contact_phone'],
            area_id=data['area_id'],
            category=data['category'],
            address_note=data['address_note'],
            description=data['description'],
            confirmed_at=None,
        )
        audit(request.user, 'ops_assisted_application_created', created.pk, applicant_id=target.pk)
    created = MerchantApplication.objects.select_related('user', 'area', 'reviewed_by').get(pk=created.pk)
    return Response({'application': _serialize_application(created)}, status=201)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([TrustedUserRateThrottle])
def review_application_view(request, application_id):
    require_ops_perm(request.user, 'market.change_merchantapplication')
    form = ApplicationReviewInput(data=request.data)
    form.is_valid(raise_exception=True)
    data = form.validated_data
    User = get_user_model()
    with transaction.atomic():
        candidate = get_object_or_404(MerchantApplication, pk=application_id)
        User.objects.select_for_update(no_key=True).get(pk=candidate.user_id)
        locked = MerchantApplication.objects.select_for_update().get(pk=application_id)
        if 'review_note' in data:
            locked.review_note = data['review_note']
            locked.save(update_fields=['review_note', 'updated_at'])
        reviewed = review_application(application_id, request.user, data['decision'])
    reviewed = MerchantApplication.objects.select_related('user', 'area', 'reviewed_by').get(pk=reviewed.pk)
    return Response({'application': _serialize_application(reviewed)})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@throttle_classes([TrustedUserRateThrottle])
def stalls_governance(request):
    require_ops_perm(
        request.user,
        'market.view_stall',
        'market.change_stall',
        'market.view_merchantprofile',
        'market.change_merchantprofile',
        any_of=True,
    )
    cfg = SiteConfiguration.current()
    query = (
        Stall.objects.select_related('merchant__user', 'area', 'location', 'current_session')
        .prefetch_related('delivery_points', 'products')
        .order_by('id')
    )
    area_param = request.query_params.get('area', '').strip()
    if area_param:
        area_id = serializers.IntegerField(min_value=1).run_validation(area_param)
        query = query.filter(area_id=area_id)
    tier = request.query_params.get('tier', 'all')
    if tier in ('mobile_vendor', 'storefront'):
        query = query.filter(merchant__qualification_tier=tier)
    elif tier != 'all':
        raise BusinessError('经营资质类型筛选无效。', 'invalid_tier', status=400)
    visibility = request.query_params.get('visibility', 'all')
    if visibility == 'visible':
        query = query.filter(is_visible=True)
    elif visibility == 'hidden':
        query = query.filter(is_visible=False)
    elif visibility != 'all':
        raise BusinessError('展示状态筛选无效。', 'invalid_visibility', status=400)
    q = request.query_params.get('q', '').strip()
    if q:
        query = query.filter(
            Q(name__icontains=q)
            | Q(merchant__business_name__icontains=q)
            | Q(merchant__user__username__icontains=q)
        )
    include_sensitive = _can_view_merchant_sensitive(request.user)
    serialized = [
        _serialize_stall_governance(s, cfg, include_merchant_sensitive=include_sensitive)
        for s in query
    ]
    eligibility = request.query_params.get('eligibility', 'all')
    if eligibility == 'trade':
        serialized = [row for row in serialized if row['transaction_eligible']]
    elif eligibility == 'discovery':
        serialized = [row for row in serialized if not row['transaction_eligible']]
    elif eligibility != 'all':
        raise BusinessError('交易资格筛选无效。', 'invalid_eligibility', status=400)

    merchants = [
        _serialize_merchant(m, include_sensitive=include_sensitive)
        for m in MerchantProfile.objects.select_related('user').order_by('id')
    ]
    areas = [
        {
            'id': a.pk,
            'name': a.name,
            'subtitle': a.subtitle,
            'latitude': a.latitude,
            'longitude': a.longitude,
            'is_demo': a.is_demo,
        }
        for a in Area.objects.order_by('id')
    ]
    points = [
        {
            'id': p.pk,
            'area_id': p.area_id,
            'area_name': p.area.name,
            'name': p.name,
            'address': p.address,
            'latitude': p.latitude,
            'longitude': p.longitude,
            'is_active': p.is_active,
            'is_simulation': p.is_simulation,
        }
        for p in DeliveryPoint.objects.select_related('area').order_by('area_id', 'id')
    ]
    return Response(
        {
            'stalls': serialized,
            'merchants': merchants,
            'areas': areas,
            'delivery_points': points,
        },
        headers={'Cache-Control': 'private, no-store'},
    )


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
@throttle_classes([TrustedUserRateThrottle])
def update_merchant(request, merchant_id):
    require_ops_perm(request.user, 'market.change_merchantprofile')
    form = MerchantUpdateInput(data=request.data)
    form.is_valid(raise_exception=True)
    data = form.validated_data
    with transaction.atomic():
        # Match checkout and StallAdmissionForm lock order: Stall(s) -> MerchantProfile.
        list(
            Stall.objects.select_for_update(no_key=True)
            .filter(merchant_id=merchant_id)
            .order_by('pk')
            .values_list('pk', flat=True)
        )
        merchant = get_object_or_404(
            MerchantProfile.objects.select_for_update(no_key=True),
            pk=merchant_id,
        )
        changed = []
        for field, value in data.items():
            if field == 'wechat_pay_account':
                value = (value or '').strip() or None
                if merchant.wechat_pay_account != value and not (
                    request.user.is_superuser or can_operate_all(request.user, 'market.resolve_payments')
                ):
                    raise BusinessError('修改微信直连收款配置标识需要支付核验或超级管理员权限。', 'forbidden', status=403)
            if getattr(merchant, field) != value:
                setattr(merchant, field, value)
                changed.append(field)
        try:
            merchant.full_clean()
        except DjangoValidationError as exc:
            _raise_django_validation(exc)
        if changed:
            try:
                merchant.save()
            except IntegrityError:
                raise BusinessError('微信收款配置标识已被其他商户绑定。', 'wechat_pay_account_taken', status=409) from None
            audit(request.user, 'ops_merchant_updated', f'market.MerchantProfile:{merchant.pk}', fields=changed)
    merchant = MerchantProfile.objects.select_related('user').get(pk=merchant.pk)
    cfg = SiteConfiguration.current()
    stalls = [
        _serialize_stall_governance(s, cfg)
        for s in Stall.objects.select_related('merchant__user', 'area', 'location', 'current_session')
        .prefetch_related('delivery_points', 'products')
        .filter(merchant=merchant)
        .order_by('id')
    ]
    return Response({'merchant': _serialize_merchant(merchant), 'stalls': stalls})


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
@throttle_classes([TrustedUserRateThrottle])
def update_stall(request, stall_id):
    require_ops_perm(request.user, 'market.change_stall')
    form = StallGovernanceInput(data=request.data)
    form.is_valid(raise_exception=True)
    data = form.validated_data
    with transaction.atomic():
        stall = get_object_or_404(Stall.objects.select_for_update(no_key=True), pk=stall_id)
        merchant = MerchantProfile.objects.select_for_update(no_key=True).get(
            pk=stall.merchant_id
        )
        stall.merchant = merchant

        target_area = stall.area
        if 'area_id' in data and data['area_id'] != stall.area_id:
            target_area = get_object_or_404(Area, pk=data['area_id'])
            if not settings.DEMO_MODE and target_area.is_demo:
                raise BusinessError('非演示环境不能分配示例校园区域。', 'invalid_area', status=400)

        location_payload = data.get('location')
        location_moved = False
        if location_payload is not None:
            loc = getattr(stall, 'location', None)
            if loc is None:
                location_moved = True
            else:
                location_moved = (
                    loc.address != location_payload['address']
                    or loc.latitude != location_payload['latitude']
                    or loc.longitude != location_payload['longitude']
                )

        grants = ('transaction_enabled', 'delivery_approved', 'delivery_enabled')
        granting = any(
            field in data and data[field] and not getattr(stall, field)
            for field in grants
        )
        if granting:
            candidate = Stall(merchant=merchant, is_demo=stall.is_demo, transaction_enabled=True)
            reason = pickup_eligibility_reason(candidate)
            if reason:
                raise BusinessError(f'不能授予交易或配送权限：{reason}', 'qualification_incomplete', status=400)

        changed = []
        for field in (
            'name',
            'category',
            'is_visible',
            'public_phone_enabled',
            'transaction_enabled',
            'delivery_approved',
            'delivery_enabled',
        ):
            if field in data and getattr(stall, field) != data[field]:
                setattr(stall, field, data[field])
                changed.append(field)

        if target_area.pk != stall.area_id:
            stall.area = target_area
            changed.append('area')

        if location_payload is not None:
            loc = getattr(stall, 'location', None) or StallLocation(stall=stall)
            if location_moved:
                loc.address = location_payload['address']
                loc.latitude = location_payload['latitude']
                loc.longitude = location_payload['longitude']
                loc.save()
                changed.append('location')
                # Moving pickup coordinates requires explicit re-authorization of online ordering.
                if 'transaction_enabled' not in data and stall.transaction_enabled:
                    stall.transaction_enabled = False
                    if 'transaction_enabled' not in changed:
                        changed.append('transaction_enabled')

        concrete_changed = [f for f in changed if f != 'location']
        if concrete_changed:
            stall.save(update_fields=[f if f != 'area' else 'area_id' for f in concrete_changed])

        if 'delivery_point_ids' in data:
            point_ids = sorted(set(data['delivery_point_ids']))
            points_qs = DeliveryPoint.objects.filter(pk__in=point_ids, area_id=stall.area_id)
            if not stall.is_demo or not settings.DEMO_MODE:
                points_qs = points_qs.filter(is_simulation=False)
            points = list(points_qs)
            if len(points) != len(point_ids):
                raise BusinessError('所选交接点必须属于该摊位所在校园区域且与运行模式匹配。', 'invalid_delivery_points', status=400)
            current_ids = sorted(stall.delivery_points.values_list('pk', flat=True))
            if current_ids != point_ids:
                stall.delivery_points.set(points)
                changed.append('delivery_points')
        elif 'area' in changed and stall.delivery_points.exclude(area_id=stall.area_id).exists():
            stall.delivery_points.set(stall.delivery_points.filter(area_id=stall.area_id))
            changed.append('delivery_points')

        if changed:
            audit(request.user, 'ops_stall_updated', f'market.Stall:{stall.pk}', fields=changed)

    refreshed = (
        Stall.objects.select_related('merchant__user', 'area', 'location', 'current_session')
        .prefetch_related('delivery_points', 'products')
        .get(pk=stall_id)
    )
    return Response(
        {
            'stall': _serialize_stall_governance(
                refreshed,
                SiteConfiguration.current(),
                include_merchant_sensitive=_can_view_merchant_sensitive(request.user),
            )
        }
    )


def _serialize_area(area: Area):
    return {
        'id': area.pk,
        'name': area.name,
        'subtitle': area.subtitle,
        'latitude': area.latitude,
        'longitude': area.longitude,
        'is_demo': area.is_demo,
        'stall_count': getattr(area, 'stall_count', 0),
        'delivery_points': [
            {
                'id': p.pk,
                'area_id': p.area_id,
                'name': p.name,
                'address': p.address,
                'latitude': p.latitude,
                'longitude': p.longitude,
                'is_active': p.is_active,
                'is_simulation': p.is_simulation,
            }
            for p in area.delivery_points.all()
        ],
    }


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([TrustedUserRateThrottle])
def areas_view(request):
    if request.method == 'GET':
        require_ops_perm(
            request.user,
            'market.view_area',
            'market.change_area',
            'market.view_deliverypoint',
            'market.change_deliverypoint',
            any_of=True,
        )
        rows = (
            Area.objects.annotate(stall_count=Count('stalls', distinct=True))
            .prefetch_related('delivery_points')
            .order_by('id')
        )
        return Response(
            {'areas': [_serialize_area(a) for a in rows]},
            headers={'Cache-Control': 'private, no-store'},
        )

    require_ops_perm(request.user, 'market.add_area')
    form = AreaWriteInput(data=request.data)
    form.is_valid(raise_exception=True)
    data = form.validated_data
    for required in ('name', 'latitude', 'longitude'):
        if required not in data:
            raise serializers.ValidationError({required: '请填写此项。'})
    with transaction.atomic():
        area = Area(
            name=data['name'].strip(),
            subtitle=data.get('subtitle', '').strip(),
            latitude=data['latitude'],
            longitude=data['longitude'],
            is_demo=bool(data.get('is_demo', False)),
        )
        try:
            area.full_clean()
        except DjangoValidationError as exc:
            _raise_django_validation(exc)
        area.save()
        audit(request.user, 'ops_area_created', f'market.Area:{area.pk}')
    area = Area.objects.annotate(stall_count=Count('stalls', distinct=True)).prefetch_related('delivery_points').get(pk=area.pk)
    return Response({'area': _serialize_area(area)}, status=201)


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
@throttle_classes([TrustedUserRateThrottle])
def area_detail_view(request, area_id):
    require_ops_perm(request.user, 'market.change_area')
    form = AreaWriteInput(data=request.data)
    form.is_valid(raise_exception=True)
    data = form.validated_data
    with transaction.atomic():
        area = get_object_or_404(Area.objects.select_for_update(), pk=area_id)
        changed = []
        for field in ('name', 'subtitle', 'latitude', 'longitude', 'is_demo'):
            if field in data:
                val = data[field].strip() if isinstance(data[field], str) else data[field]
                if getattr(area, field) != val:
                    setattr(area, field, val)
                    changed.append(field)
        try:
            area.full_clean()
        except DjangoValidationError as exc:
            _raise_django_validation(exc)
        if changed:
            area.save(update_fields=changed)
            audit(request.user, 'ops_area_updated', f'market.Area:{area.pk}', fields=changed)
    area = Area.objects.annotate(stall_count=Count('stalls', distinct=True)).prefetch_related('delivery_points').get(pk=area.pk)
    return Response({'area': _serialize_area(area)})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([TrustedUserRateThrottle])
def delivery_points_view(request):
    require_ops_perm(request.user, 'market.add_deliverypoint')
    form = DeliveryPointWriteInput(data=request.data)
    form.is_valid(raise_exception=True)
    data = form.validated_data
    for required in ('area_id', 'name', 'address', 'latitude', 'longitude'):
        if required not in data:
            raise serializers.ValidationError({required: '请填写此项。'})
    with transaction.atomic():
        area = get_object_or_404(Area, pk=data['area_id'])
        if not settings.DEMO_MODE and area.is_demo:
            raise BusinessError('非演示环境不能在示例区域创建交接点。', 'invalid_area', status=400)
        point = DeliveryPoint(
            area=area,
            name=data['name'].strip(),
            address=data['address'].strip(),
            latitude=data['latitude'],
            longitude=data['longitude'],
            is_active=bool(data.get('is_active', False)),
            is_simulation=False,
        )
        try:
            point.full_clean()
        except DjangoValidationError as exc:
            _raise_django_validation(exc)
        point.save()
        audit(request.user, 'ops_delivery_point_created', f'market.DeliveryPoint:{point.pk}', area_id=area.pk)
    return Response(
        {
            'delivery_point': {
                'id': point.pk,
                'area_id': point.area_id,
                'name': point.name,
                'address': point.address,
                'latitude': point.latitude,
                'longitude': point.longitude,
                'is_active': point.is_active,
                'is_simulation': point.is_simulation,
            }
        },
        status=201,
    )


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
@throttle_classes([TrustedUserRateThrottle])
def delivery_point_detail_view(request, point_id):
    require_ops_perm(request.user, 'market.change_deliverypoint')
    form = DeliveryPointWriteInput(data=request.data)
    form.is_valid(raise_exception=True)
    data = form.validated_data
    with transaction.atomic():
        point = get_object_or_404(DeliveryPoint.objects.select_for_update(), pk=point_id)
        changed = []
        if 'area_id' in data and data['area_id'] != point.area_id:
            area = get_object_or_404(Area, pk=data['area_id'])
            if not settings.DEMO_MODE and area.is_demo:
                raise BusinessError('非演示环境不能分配示例校园区域。', 'invalid_area', status=400)
            if point.stalls.exclude(area_id=area.pk).exists():
                raise BusinessError('已有原区域摊位关联该交接点，请先解除摊位关联后再变更所属区域。', 'delivery_point_in_use', status=409)
            point.area = area
            changed.append('area_id')
        for field in ('name', 'address', 'latitude', 'longitude', 'is_active'):
            if field in data:
                val = data[field].strip() if isinstance(data[field], str) else data[field]
                if getattr(point, field) != val:
                    setattr(point, field, val)
                    changed.append(field)
        try:
            point.full_clean()
        except DjangoValidationError as exc:
            _raise_django_validation(exc)
        if changed:
            point.save(update_fields=changed)
            audit(request.user, 'ops_delivery_point_updated', f'market.DeliveryPoint:{point.pk}', fields=changed)
    return Response(
        {
            'delivery_point': {
                'id': point.pk,
                'area_id': point.area_id,
                'name': point.name,
                'address': point.address,
                'latitude': point.latitude,
                'longitude': point.longitude,
                'is_active': point.is_active,
                'is_simulation': point.is_simulation,
            }
        }
    )


def _serialize_ops_order_summary(order: Order, request):
    data = OrderSerializer(order, context={'merchant': True, 'request': request}).data
    data['allowed_actions'] = []
    data['stall_payment_qr_image'] = ''
    data['buyer_username'] = order.user.username
    data['buyer_display_name'] = order.user.first_name or order.user.username
    return data


def _serialize_ops_order_detail(order: Order, request):
    summary = _serialize_ops_order_summary(order, request)
    payments = [
        {
            'id': str(p.pk),
            'mode': p.mode,
            'out_trade_no': p.out_trade_no,
            'transaction_id': p.transaction_id or '',
            'channel': p.channel,
            'status': p.status,
            'amount_cents': p.amount_cents,
            'created_at': p.created_at.isoformat(),
            'paid_at': p.paid_at.isoformat() if p.paid_at else None,
            'expires_at': p.expires_at.isoformat(),
            'next_query_at': p.next_query_at.isoformat() if p.next_query_at else None,
            'error_code': p.error_code,
            'error_message': p.error_message,
        }
        for p in order.payments.all()
    ]
    refunds = [
        {
            'id': str(r.pk),
            'payment_id': str(r.payment_id),
            'mode': r.mode,
            'out_refund_no': r.out_refund_no,
            'refund_id': r.refund_id or '',
            'source': r.source,
            'status': r.status,
            'amount_cents': r.amount_cents,
            'reason': r.reason,
            'created_at': r.created_at.isoformat(),
            'completed_at': r.completed_at.isoformat() if r.completed_at else None,
            'resolved_at': r.resolved_at.isoformat() if r.resolved_at else None,
            'error_code': r.error_code,
            'error_message': r.error_message,
        }
        for r in order.refunds.all()
    ]
    evidence = [
        {
            'id': str(e.pk),
            'operation': e.operation,
            'outcome': e.outcome,
            'reason': e.reason,
            'facts': e.facts,
            'actor_username': e.actor.username if e.actor_id and e.actor else '',
            'created_at': e.created_at.isoformat(),
        }
        for e in order.financial_evidence.select_related('actor')[:50]
    ]
    return {'order': summary, 'payments': payments, 'refunds': refunds, 'evidence': evidence}


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@throttle_classes([TrustedUserRateThrottle])
def orders_view(request):
    require_ops_perm(request.user, 'market.view_order')
    query = order_query().select_related('user')
    mode = request.query_params.get('mode', 'all')
    if mode in ('live', 'simulation'):
        query = query.filter(mode=mode)
    elif mode != 'all':
        raise BusinessError('订单模式筛选无效。', 'invalid_mode', status=400)

    stall_param = request.query_params.get('stall', '').strip()
    if stall_param:
        stall_id = serializers.IntegerField(min_value=1).run_validation(stall_param)
        query = query.filter(stall_id=stall_id)

    q = request.query_params.get('q', '').strip()
    if q:
        query = query.filter(
            Q(number__icontains=q)
            | Q(user__username__icontains=q)
            | Q(stall_name__icontains=q)
        )

    selected = request.query_params.get('filter', 'attention')
    scoped, filters = scope_orders(query, merchant=True)
    if selected not in filters:
        raise BusinessError('订单筛选无效。', 'invalid_filter', status=400)

    try:
        size = int(request.query_params.get('page_size', '25'))
        if not 1 <= size <= 100:
            raise ValueError
    except (TypeError, ValueError):
        raise BusinessError('每页数量应为 1 至 100。', 'invalid_page_size', status=400) from None

    scope = ['ops', request.user.pk, mode, stall_param, q, selected]
    cursor = request.query_params.get('cursor')
    position = None
    if cursor:
        try:
            if len(cursor) > 2048:
                raise ValueError
            value = signing.loads(cursor, salt=OPS_CURSOR_SALT, max_age=86400)
            created = parse_datetime(value['created'])
            key = uuid.UUID(value['id'])
            if value['scope'] != scope or created is None or created.tzinfo is None:
                raise ValueError
            position = Q(created_at__lt=created) | Q(created_at=created, pk__lt=key)
        except (signing.BadSignature, ValueError, TypeError, KeyError):
            raise BusinessError('列表位置已失效，请刷新列表。', 'invalid_cursor', status=400) from None

    counts = scoped.aggregate(
        reviewed=Count('pk', filter=Q(review__isnull=False)),
        **{name: Count('pk', filter=cond) for name, cond in filters.items()},
    )
    selected_qs = scoped.filter(filters[selected])
    if position is not None:
        selected_qs = selected_qs.filter(position)
    rows = list(selected_qs.order_by('-created_at', '-pk')[: size + 1])
    next_cursor = None
    if len(rows) > size:
        tail = rows[size - 1]
        next_cursor = signing.dumps(
            {'created': tail.created_at.isoformat(), 'id': str(tail.pk), 'scope': scope},
            salt=OPS_CURSOR_SALT,
            compress=True,
        )
    return Response(
        {
            'results': [_serialize_ops_order_summary(row, request) for row in rows[:size]],
            'next': next_cursor,
            'counts': counts,
        },
        headers={'Cache-Control': 'private, no-store'},
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@throttle_classes([TrustedUserRateThrottle])
def order_detail_view(request, order_id):
    require_ops_perm(request.user, 'market.view_order')
    order = get_object_or_404(order_query().select_related('user'), pk=order_id)
    return Response(
        _serialize_ops_order_detail(order, request),
        headers={'Cache-Control': 'private, no-store'},
    )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([TrustedUserRateThrottle])
def financial_resolution_view(request, order_id):
    require_staff(request.user)
    require_operator(request.user, 'market.resolve_payments')
    order = get_object_or_404(Order, pk=order_id)
    form = FinancialResolutionInput(data=request.data)
    form.is_valid(raise_exception=True)
    values = form.validated_data
    op = values['operation']
    payment_id = values.get('payment_id')
    refund_id = values.get('refund_id')
    if payment_id is not None:
        payment = order.payments.filter(pk=payment_id, mode='live').first()
        if payment is None:
            raise BusinessError('所选付款记录不存在或不属于该正式订单。', 'not_found', status=404)
        if op in ('retry', 'compensation') and values['expected_amount_cents'] != payment.amount_cents:
            raise BusinessError('确认退款金额必须与所选原付款的全额金额一致。', 'refund_amount_changed', status=400)
        if op == 'retry':
            refund = order.refunds.filter(pk=refund_id, payment=payment, mode='live', status='closed', resolved_at__isnull=True).first()
            if refund is None:
                raise BusinessError('请选择属于所选原付款且处于关闭未结案状态的退款记录。', 'refund_retry_unavailable', status=400)

    if op == 'verify':
        verify_and_resolve(order.pk, request.user, values['reason'])
    elif op == 'uncreated':
        confirm_uncreated_payment(order.pk, payment_id, request.user, values['reason'])
    elif op == 'external':
        verify_external_refund(order.pk, payment_id, values['out_refund_no'].strip(), request.user, values['reason'])
    else:
        request_operator_refund(
            order.pk,
            payment_id,
            request.user,
            values['reason'],
            values['operation_key'],
            values['expected_amount_cents'],
            replaces_id=refund_id if op == 'retry' else None,
        )
    refreshed = order_query().select_related('user').get(pk=order.pk)
    return Response(_serialize_ops_order_detail(refreshed, request))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@throttle_classes([TrustedUserRateThrottle])
def reconciliation_export_view(request):
    require_staff(request.user)
    require_operator(request.user, 'market.export_financial_reconciliation')
    merchant_id = serializers.IntegerField(min_value=1).run_validation(request.query_params.get('merchant_id'))
    get_object_or_404(MerchantProfile, pk=merchant_id)
    starts_on = parse_date(request.query_params.get('starts_on') or '')
    ends_on = parse_date(request.query_params.get('ends_on') or '')
    if starts_on is None or ends_on is None:
        raise BusinessError('请填写有效的开始与结束日期（YYYY-MM-DD）。', 'invalid_date_range', status=400)
    return reconciliation_export(request.user, merchant_id, starts_on, ends_on)


def _serialize_feedback(row: Feedback):
    loc = getattr(row.stall, 'location', None) if row.stall_id else None
    return {
        'id': row.pk,
        'kind': row.kind,
        'stall_id': row.stall_id,
        'stall_name': row.stall.name if row.stall_id and row.stall else '',
        'current_stall_address': loc.address if loc else '',
        'order_id': str(row.order_id) if row.order_id else None,
        'order_number': row.order.number if row.order_id and row.order else '',
        'username': row.user.username if row.user_id and row.user else '游客提交',
        'content': row.content,
        'contact': row.contact,
        'location_snapshot': row.location_snapshot or {},
        'verification': row.verification,
        'verification_note': row.verification_note,
        'resolved': row.resolved,
        'created_at': row.created_at.isoformat(),
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@throttle_classes([TrustedUserRateThrottle])
def feedback_list(request):
    require_ops_perm(request.user, 'market.view_feedback', 'market.change_feedback', any_of=True)
    base = Feedback.objects.select_related('stall__location', 'order', 'user')
    counts = base.aggregate(
        all=Count('pk'),
        unresolved=Count('pk', filter=Q(resolved=False)),
        unreviewed=Count('pk', filter=~Q(kind='general') & Q(verification='unreviewed')),
    )
    status_filter = request.query_params.get('status', 'unresolved')
    if status_filter == 'unresolved':
        base = base.filter(resolved=False)
    elif status_filter == 'unreviewed':
        base = base.exclude(kind='general').filter(verification='unreviewed')
    elif status_filter != 'all':
        raise BusinessError('反馈筛选状态无效。', 'invalid_status', status=400)

    kind = request.query_params.get('kind', 'all')
    valid_kinds = {item[0] for item in Feedback.KINDS}
    if kind != 'all':
        if kind not in valid_kinds:
            raise BusinessError('反馈类型筛选无效。', 'invalid_kind', status=400)
        base = base.filter(kind=kind)

    stall_param = request.query_params.get('stall', '').strip()
    if stall_param:
        stall_id = serializers.IntegerField(min_value=1).run_validation(stall_param)
        base = base.filter(stall_id=stall_id)

    rows = list(base.order_by('resolved', '-created_at', '-pk')[:100])
    return Response(
        {'counts': counts, 'results': [_serialize_feedback(r) for r in rows]},
        headers={'Cache-Control': 'private, no-store'},
    )


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
@throttle_classes([TrustedUserRateThrottle])
def feedback_detail_view(request, feedback_id):
    require_ops_perm(request.user, 'market.change_feedback')
    form = FeedbackReviewInput(data=request.data)
    form.is_valid(raise_exception=True)
    data = form.validated_data
    with transaction.atomic():
        row = get_object_or_404(Feedback.objects.select_for_update(), pk=feedback_id)
        next_verification = data.get('verification', row.verification)
        next_note = data.get('verification_note', row.verification_note)
        if next_verification in ('confirmed', 'dismissed') and not next_note.strip():
            raise serializers.ValidationError({'verification_note': ['请记录核实方式与结论依据，处理完成本身不代表已核实。']})
        changed = []
        for field in ('verification', 'verification_note', 'resolved'):
            if field in data and getattr(row, field) != data[field]:
                setattr(row, field, data[field])
                changed.append(field)
        if changed:
            row.save(update_fields=changed)
            audit(
                request.user,
                'ops_feedback_updated',
                f'market.Feedback:{row.pk}',
                fields=changed,
                verification=row.verification,
                resolved=row.resolved,
            )
    row = Feedback.objects.select_related('stall__location', 'order', 'user').get(pk=row.pk)
    return Response({'feedback': _serialize_feedback(row)})


def _user_has_active_obligations(target_user):
    if (
        target_user.orders.filter(
            Q(status__in=OPEN_ORDER_STATUSES)
            | Q(payment_status='refunding')
            | Q(payment_review_required=True)
        ).exists()
        or PaymentAttempt.objects.filter(order__user=target_user, status__in=PaymentAttempt.ACTIVE_STATUSES).exists()
        or PaymentRefund.objects.filter(order__user=target_user, resolved_at__isnull=True).exclude(status='success').exists()
    ):
        return True
    if MerchantProfile.objects.filter(user=target_user).exists():
        merchant_orders = Order.objects.filter(stall__merchant__user=target_user)
        if (
            merchant_orders.filter(
                Q(status__in=OPEN_ORDER_STATUSES)
                | Q(payment_status='refunding')
                | Q(payment_review_required=True)
            ).exists()
            or PaymentAttempt.objects.filter(merchant__user=target_user, status__in=PaymentAttempt.ACTIVE_STATUSES).exists()
            or PaymentRefund.objects.filter(payment__merchant__user=target_user, resolved_at__isnull=True)
            .exclude(status='success')
            .exists()
        ):
            return True
    return False


def _serialize_user(row):
    merchant = getattr(row, 'merchant_profile', None)
    app = getattr(row, 'merchant_application', None)
    return {
        'id': row.pk,
        'username': row.username,
        'display_name': row.first_name or row.username,
        'is_active': row.is_active,
        'is_staff': row.is_staff,
        'is_superuser': row.is_superuser,
        'is_merchant': merchant is not None,
        'business_name': merchant.business_name if merchant else '',
        'application_status': app.status if app else None,
        'date_joined': row.date_joined.isoformat() if row.date_joined else None,
        'last_login': row.last_login.isoformat() if row.last_login else None,
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@throttle_classes([TrustedUserRateThrottle])
def users_list(request):
    require_ops_perm(request.user, 'auth.view_user', 'auth.change_user', any_of=True)
    User = get_user_model()
    base = User.objects.select_related('merchant_profile', 'merchant_application').annotate(
        _has_merchant=Exists(MerchantProfile.objects.filter(user_id=OuterRef('pk')))
    )
    counts = base.aggregate(
        all=Count('pk'),
        staff=Count('pk', filter=Q(is_staff=True)),
        merchant=Count('pk', filter=Q(_has_merchant=True)),
        student=Count('pk', filter=Q(is_staff=False, _has_merchant=False)),
        inactive=Count('pk', filter=Q(is_active=False)),
    )
    role = request.query_params.get('role', 'all')
    if role == 'staff':
        base = base.filter(is_staff=True)
    elif role == 'merchant':
        base = base.filter(_has_merchant=True)
    elif role == 'student':
        base = base.filter(is_staff=False, _has_merchant=False)
    elif role != 'all':
        raise BusinessError('角色筛选无效。', 'invalid_role', status=400)

    q = request.query_params.get('q', '').strip()
    if q:
        base = base.filter(
            Q(username__icontains=q)
            | Q(first_name__icontains=q)
            | Q(merchant_profile__business_name__icontains=q)
        )
    rows = list(base.order_by('-is_staff', '-id')[:100])
    return Response(
        {'counts': counts, 'results': [_serialize_user(r) for r in rows]},
        headers={'Cache-Control': 'private, no-store'},
    )


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
@throttle_classes([TrustedUserRateThrottle])
def user_detail_view(request, user_id):
    require_ops_perm(request.user, 'auth.change_user')
    form = UserGovernanceInput(data=request.data)
    form.is_valid(raise_exception=True)
    data = form.validated_data
    User = get_user_model()
    with transaction.atomic():
        target = get_object_or_404(User.objects.select_for_update(), pk=user_id)
        if target.is_superuser and not request.user.is_superuser:
            raise BusinessError('普通运营人员不能修改超级管理员账号。', 'forbidden', status=403)
        if 'is_active' in data and not data['is_active']:
            if target.pk == request.user.pk:
                raise BusinessError('不能停用当前正在使用的管理员账号。', 'self_deactivation_forbidden', status=400)
            if target.is_superuser:
                raise BusinessError('不能在管理台直接停用超级管理员账号。', 'superuser_protected', status=400)
            if _user_has_active_obligations(target):
                raise BusinessError('该账号仍有进行中订单或未结清资金记录，处理完结前不能停用。', 'active_orders', status=409)
        changed = []
        if 'is_active' in data and target.is_active != data['is_active']:
            target.is_active = data['is_active']
            changed.append('is_active')
        if 'display_name' in data:
            next_name = data['display_name'].strip()
            if target.first_name != next_name:
                target.first_name = next_name
                changed.append('first_name')
        if changed:
            target.save(update_fields=changed)
            audit(request.user, 'ops_user_updated', f'auth.User:{target.pk}', fields=changed, is_active=target.is_active)
    target = User.objects.select_related('merchant_profile', 'merchant_application').get(pk=target.pk)
    return Response({'user': _serialize_user(target)})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@throttle_classes([TrustedUserRateThrottle])
def audit_logs(request):
    require_ops_perm(request.user, 'market.view_auditlog')
    base = AuditLog.objects.select_related('actor')
    action = request.query_params.get('action', '').strip()
    if action:
        base = base.filter(action=action)
    q = request.query_params.get('q', '').strip()
    if q:
        base = base.filter(Q(target__icontains=q) | Q(actor__username__icontains=q) | Q(action__icontains=q))
    rows = list(base.order_by('-created_at', '-pk')[:100])
    actions = list(
        AuditLog.objects.order_by('action').values_list('action', flat=True).distinct()[:60]
    )
    return Response(
        {
            'actions': actions,
            'results': [
                {
                    'id': row.pk,
                    'created_at': row.created_at.isoformat(),
                    'actor_username': row.actor.username if row.actor_id and row.actor else '系统',
                    'action': row.action,
                    'target': row.target,
                    'details': row.details or {},
                }
                for row in rows
            ],
        },
        headers={'Cache-Control': 'private, no-store'},
    )
