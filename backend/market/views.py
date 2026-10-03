import math
import secrets
from django.conf import settings
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction, connection
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.db.models.expressions import RawSQL
from django.middleware.csrf import get_token
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.exceptions import Throttled
from .errors import BusinessError
from .models import Area, BusinessSession, Event, Feedback, Follow, Order, Review, SiteConfiguration, Stall, StallLocation
from .serializers import AreaSerializer, OrderInput, OrderSerializer, RegistrationInput, StallCutoffInput, StallSerializer, StallStatusInput, user_data
from .services import audit, cancel_order, confirm_receipt, create_order, expire_pending_orders, merchant_action
from .auth_limits import client_ip, login_attempt, LoginRateLimited
from .permissions import get_merchant_stall, owned_or_permitted
from .discovery_queries import filter_status, visible_stall_query
from .list_api import order_list_response


class AuthThrottle(AnonRateThrottle):
    scope = 'auth'
    def get_ident(self, request):
        return client_ip(request)
    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': self.get_ident(request)}


def csrf(request): SessionAuthentication().enforce_csrf(request)


def check_password(password, user):
    try: validate_password(password, user)
    except DjangoValidationError as exc:
        raise BusinessError(' '.join(exc.messages), 'weak_password', status=400)


def visible_stalls():
    return visible_stall_query()


def stall_context(request):
    context = {'config': SiteConfiguration.current(), 'follow_ids': set()}
    if request.user.is_authenticated:
        context['follow_ids'] = set(Follow.objects.filter(user=request.user).values_list('stall_id', flat=True))
    if 'lat' in request.query_params or 'lng' in request.query_params:
        try:
            lat, lng = float(request.query_params['lat']), float(request.query_params['lng'])
            if not math.isfinite(lat) or not math.isfinite(lng) or not -90 <= lat <= 90 or not -180 <= lng <= 180: raise ValueError
            context['point'] = (lat, lng)
        except (ValueError, KeyError): raise BusinessError('定位坐标无效。', 'invalid_location', status=400)
    return context


def serialize_stall(stall, request): return StallSerializer(stall, context=stall_context(request)).data


def order_query():
    return Order.objects.select_related('stall__location', 'stall__merchant', 'review__user').prefetch_related('items', 'payments', 'refunds')


def merchant_stall(stall_id, user, locked=False, permission='market.view_stall'):
    return get_merchant_stall(stall_id, user, permission, locked)


@api_view(['GET'])
def config(request):
    areas = Area.objects.all()
    if not settings.DEMO_MODE: areas = areas.filter(is_demo=False)
    get_token(request)
    from .simulation import enabled
    return Response({'demo_mode': settings.DEMO_MODE, 'services_simulation_enabled': enabled(), 'brand': settings.BRAND_NAME,
        'stale_minutes': SiteConfiguration.current().stale_minutes, 'public_base_url': settings.PUBLIC_BASE_URL,
        'amap_key': settings.AMAP_KEY if settings.AMAP_SECURITY_CODE else '', 'amap_proxy': settings.AMAP_PROXY,
        'areas': AreaSerializer(areas, many=True).data, 'user': user_data(request.user)})


@api_view(['GET'])
def me(request): return JsonResponse(user_data(request.user), safe=False)


@api_view(['GET'])
def csrf_token(request): return Response({'csrfToken': get_token(request)})


@api_view(['POST'])
@throttle_classes([])
def login_view(request):
    csrf(request)
    username, password = request.data.get('username', ''), request.data.get('password', '')
    if not isinstance(username, str) or not isinstance(password, str) or len(username) > 150 or len(password) > 128:
        raise BusinessError('账号或密码格式不正确。', status=400)
    try:
        user = login_attempt(request, username, lambda: authenticate(request, username=username, password=password))
    except LoginRateLimited as exc:
        raise Throttled(wait=exc.retry_after, detail='登录失败次数过多，请稍后再试。')
    if not user: raise BusinessError('账号或密码不正确。', 'invalid_credentials', status=400)
    login(request, user)
    return Response(user_data(user))


@api_view(['POST'])
@throttle_classes([AuthThrottle])
def register(request):
    csrf(request)
    form = RegistrationInput(data=request.data)
    form.is_valid(raise_exception=True)
    data = form.validated_data
    user = User(username=data['username'], first_name=data.get('display_name') or data['username'])
    check_password(data['password'], user)
    user.set_password(data['password'])
    try:
        with transaction.atomic(): user.save()
    except IntegrityError: raise BusinessError('这个用户名已被使用。', 'username_taken', status=400)
    login(request, user)
    return Response(user_data(user), status=201)


@api_view(['POST'])
def logout_view(request):
    csrf(request)
    logout(request)
    return Response({'detail': '已退出登录。'})


@api_view(['PATCH'])
@permission_classes([IsAuthenticated])
def profile(request):
    name = serializers.CharField(max_length=30).run_validation(request.data.get('display_name'))
    request.user.first_name = name
    request.user.save(update_fields=['first_name'])
    return Response(user_data(request.user))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([AuthThrottle])
def password(request):
    old = serializers.CharField(max_length=128).run_validation(request.data.get('old_password'))
    new = serializers.CharField(max_length=128).run_validation(request.data.get('new_password'))
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=request.user.pk)
        if not user.is_active or not user.check_password(old):
            raise BusinessError('原密码不正确。', 'invalid_password', status=400)
        check_password(new, user)
        user.set_password(new)
        user.save(update_fields=['password'])
    update_session_auth_hash(request, user)
    return Response({'detail': '密码已更新，其他设备需要重新登录。'})


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def account(request):
    from .models import PaymentAttempt, PaymentRefund
    expire_pending_orders(user_id=request.user.pk)
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=request.user.pk)
        if user.is_staff or hasattr(user, 'merchant_profile'):
            raise BusinessError('商家与运营账号请联系平台处理注销。', 'managed_account')
        if (user.orders.filter(Q(status__in=['pending_payment', 'pending', 'preparing', 'ready', 'delivering', 'arrived'])
                | Q(payment_status='refunding') | Q(payment_review_required=True)).exists()
                or PaymentAttempt.objects.filter(order__user=user, status__in=PaymentAttempt.ACTIVE_STATUSES).exists()
                or PaymentRefund.objects.filter(order__user=user, resolved_at__isnull=True).exclude(status='success').exists()):
            raise BusinessError('还有进行中的订单，请处理完成后再注销。', 'active_orders')
        supplied = serializers.CharField(max_length=128).run_validation(request.data.get('password'))
        if not user.check_password(supplied): raise BusinessError('请输入正确密码确认注销。', 'invalid_password', status=400)
        audit(user, 'account_deactivated', user.id)
        Follow.objects.filter(user=user).delete()
        user.orders.update(contact_phone='', recipient_name='', note='')
        from .models import OrderItem
        from .recovery_models import AccountRecovery
        for item in OrderItem.objects.filter(order__user=user).exclude(portions=[]):
            item.portions = [{**portion, 'note': ''} for portion in item.portions]
            item.save(update_fields=['portions'])
        AccountRecovery.objects.filter(user=user).update(code_hash='', password_stamp='', used_at=timezone.now())
        Feedback.objects.filter(user=user).update(contact='')
        user.username = 'deleted_' + secrets.token_hex(12)
        user.first_name, user.last_name, user.email, user.is_active = '已注销用户', '', '', False
        user.set_unusable_password()
        user.save()
    logout(request)
    return Response({'detail': '账号已注销；必要交易记录保留，账号信息已去标识化。'})


@api_view(['GET'])
def stalls(request):
    query = visible_stalls()
    area = request.query_params.get('area')
    if area:
        try: query = query.filter(area_id=int(area))
        except ValueError: raise BusinessError('校园区域无效。', status=400)
    q = request.query_params.get('q', '').strip()[:100]
    if q: query = query.filter(Q(name__icontains=q) | Q(products__name__icontains=q, products__is_active=True) | Q(description__icontains=q)).distinct()
    category = request.query_params.get('category')
    if category and category != 'all': query = query.filter(category=category)
    context = stall_context(request)
    status_filter = request.query_params.get('status')
    query = filter_status(query, status_filter, context['config'])
    if context.get('point') and connection.vendor == 'postgresql':
        lat, lng = context['point']
        query = query.annotate(distance_m_db=RawSQL(
            '(SELECT ST_Distance(coordinates, ST_SetSRID(ST_MakePoint(%s, %s),4326)::geography) '
            'FROM market_stalllocation WHERE stall_id = market_stall.id)', (lng, lat)))
    candidates = list(query.order_by('id'))
    if status_filter == 'orderable':
        candidates = [stall for stall in candidates if stall.can_order(context['config'])]
    elif status_filter and status_filter != 'all':
        candidates = [stall for stall in candidates if stall.effective_status(context['config']) == status_filter]
    data = list(StallSerializer(candidates, many=True, context=context).data)
    sort = request.query_params.get('sort')
    if sort == 'distance' and context.get('point'):
        data.sort(key=lambda x: x['distance_m'] if x['distance_m'] is not None else float('inf'))
    elif sort in ('rating', 'popular'):
        data.sort(key=lambda x: (x['rating'] or 0, x['review_count']), reverse=True)
    elif not sort or sort in ('recommended', 'freshness'):
        # Fresh open stalls first, including stalls that only accept walk-ins.
        data.sort(key=lambda x: (x['last_confirmed_at'] or '', -x['id']), reverse=True)
        data.sort(key=lambda x: {'open': 0, 'paused': 1, 'stale': 2, 'closed': 3}.get(x['status'], 4))
    return Response(data)


@api_view(['GET'])
def stall_detail(request, stall_id): return Response(serialize_stall(get_object_or_404(visible_stalls(), pk=stall_id), request))


@api_view(['POST', 'DELETE'])
@permission_classes([IsAuthenticated])
def follow(request, stall_id):
    stall = get_object_or_404(visible_stalls(), pk=stall_id)
    if request.method == 'POST': Follow.objects.get_or_create(user=request.user, stall=stall)
    else: Follow.objects.filter(user=request.user, stall=stall).delete()
    return Response(serialize_stall(stall, request))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def follows(request):
    ids = Follow.objects.filter(user=request.user).values_list('stall_id', flat=True)
    return Response(StallSerializer(visible_stalls().filter(id__in=ids), many=True, context=stall_context(request)).data)


@api_view(['POST'])
def event(request):
    csrf(request)
    from .operations import EventInput
    form = EventInput(data=request.data)
    form.is_valid(raise_exception=True)
    data = form.validated_data
    stall = get_object_or_404(visible_stalls(), pk=data['stall_id']) if data.get('stall_id') else None
    Event.objects.create(type=data['type'], user=request.user if request.user.is_authenticated else None,
        stall=stall, metadata=data['metadata'])
    return Response({'ok': True}, status=201)


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def orders(request):
    if request.method == 'GET':
        return order_list_response(order_query().filter(user=request.user), request)
    form = OrderInput(data=request.data)
    form.is_valid(raise_exception=True)
    order, created = create_order(request.user, form.validated_data)
    return Response(OrderSerializer(order, context={'request': request}).data, status=201 if created else 200)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def active_order_summary(request):
    active = Order.objects.filter(user=request.user, status__in=['pending_payment', 'pending', 'preparing', 'ready', 'delivering', 'arrived'])
    counts = active.aggregate(**{status: Count('id', filter=Q(status=status))
        for status in ['pending_payment', 'pending', 'preparing', 'ready', 'delivering', 'arrived']})
    counts['total'] = sum(counts.values())
    # Put food that is ready first, followed by orders still waiting for acceptance.
    # The summary deliberately excludes pickup codes, contact details and order items.
    order = active.annotate(priority=Case(
        When(status='arrived', then=Value(0)), When(status='ready', then=Value(1)), When(status='pending_payment', then=Value(2)), When(status='pending', then=Value(3)),
        When(status='delivering', then=Value(4)), default=Value(5), output_field=IntegerField(),
    )).order_by('priority', 'created_at', 'pk').values(
        'id', 'stall__name', 'status', 'fulfillment_type', 'cancel_requested', 'created_at', 'expires_at',
    ).first()
    if order:
        order['id'] = str(order['id'])
        order['stall_name'] = order.pop('stall__name')
    response = Response({'user_id': request.user.pk, 'counts': counts, 'order': order})
    response['Cache-Control'] = 'private, no-store'
    return response


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def order_detail(request, order_id):
    return Response(OrderSerializer(get_object_or_404(order_query(), pk=order_id, user=request.user), context={'request': request}).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def cancel(request, order_id):
    reason = serializers.CharField(max_length=200, allow_blank=True).run_validation(request.data.get('reason', ''))
    return Response(OrderSerializer(cancel_order(order_id, request.user, reason), context={'request': request}).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def receive_delivery(request, order_id):
    return Response(OrderSerializer(confirm_receipt(order_id, request.user), context={'request': request}).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def review(request, order_id):
    rating = serializers.IntegerField(min_value=1, max_value=5).run_validation(request.data.get('rating'))
    content = serializers.CharField(max_length=500, allow_blank=True).run_validation(request.data.get('content', ''))
    with transaction.atomic():
        order = get_object_or_404(Order.objects.select_for_update(), pk=order_id, user=request.user)
        if order.status != 'completed': raise BusinessError('完成取餐后才能评价。', 'not_completed')
        if Review.objects.filter(order=order).exists(): raise BusinessError('此订单已评价。', 'already_reviewed')
        Review.objects.create(order=order, user=request.user, stall=order.stall, rating=rating, content=content)
    return Response(OrderSerializer(order, context={'request': request}).data, status=201)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def merchant_orders(request):
    query = owned_or_permitted(order_query(), request.user, 'market.view_order')
    if request.query_params.get('stall'):
        stall_id = serializers.IntegerField(min_value=1).run_validation(request.query_params['stall'])
        query = query.filter(stall_id=stall_id)
    return order_list_response(query, request, merchant=True)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def merchant_status(request, stall_id):
    cutoff_only = 'cutoff_only' in request.data
    form = (StallCutoffInput if cutoff_only else StallStatusInput)(data=request.data)
    form.is_valid(raise_exception=True)
    data = form.validated_data
    with transaction.atomic():
        stall = merchant_stall(stall_id, request.user, locked=True, permission='market.change_stall')
        if cutoff_only:
            # The stall lock serializes this check with closing/reopening on other
            # devices. Never replay stale status or position fields from the page.
            session = stall.current_session
            if not session or session.pk != data['expected_session_id']:
                raise BusinessError('营业场次已变化，请刷新后核对本场设置。', 'business_session_changed')
            if session.status == 'closed' or (session.closes_at and session.closes_at <= timezone.now()):
                raise BusinessError('本场营业已结束，请重新开摊后设置接单截止。', 'business_session_ended')
            cutoff = data['stop_orders_at']
            if cutoff and session.closes_at and cutoff > session.closes_at:
                raise BusinessError('停止接单时间不能晚于预计收摊时间。', 'invalid_stop_orders_at', status=400)
            session.stop_orders_at = cutoff
            session.save(update_fields=['stop_orders_at'])
            audit(request.user, 'stall_cutoff', stall.pk, business_session_id=session.pk,
                stop_orders_at=cutoff.isoformat() if cutoff else None)
            return Response(StallSerializer(stall, context={**stall_context(request), 'merchant': True}).data)
        location = getattr(stall, 'location', None)
        location_fields = ('address', 'latitude', 'longitude')
        if location is None:
            missing = [field for field in location_fields if field not in data]
            if missing:
                raise BusinessError('首次设置位置请填写取餐地址和完整经纬度。', 'location_required', status=400,
                    fields=missing)
            # The stall lock serializes first-time creation with other location
            # writes. First location entry needs the same approval as a move.
            location = StallLocation(stall=stall)
            location_changed = True
        else:
            location_changed = any(field in data and data[field] != getattr(location, field) for field in location_fields)
        if location_changed:
            for field in location_fields:
                if field in data: setattr(location, field, data[field])
            location.save()
            stall.transaction_enabled = False
            audit(request.user, 'location_changed_requires_approval', stall.id)
        previous = stall.current_session
        ended = not previous or previous.status == 'closed' or (previous.closes_at and previous.closes_at <= timezone.now())
        if data['status'] == 'open' and ended:
            if not data['confirm_location']: raise BusinessError('开摊前请确认当前位置。', 'confirm_location_required', status=400)
            session = BusinessSession.objects.create(stall=stall, status='open', closes_at=data.get('closes_at'), stop_orders_at=data.get('stop_orders_at'))
        else:
            session = previous or BusinessSession.objects.create(stall=stall, status=data['status'])
            session.status = data['status']
            if data['confirm_location']: session.last_confirmed_at = timezone.now()
            if 'closes_at' in data: session.closes_at = data['closes_at']
            if 'stop_orders_at' in data: session.stop_orders_at = data['stop_orders_at']
            session.save()
        if session.closes_at and session.closes_at <= timezone.now() and data['status'] == 'open':
            raise BusinessError('预计收摊时间必须晚于当前时间。', 'invalid_closing_time', status=400)
        if session.stop_orders_at and session.closes_at and session.stop_orders_at > session.closes_at:
            raise BusinessError('停止接单时间不能晚于预计收摊时间。', 'invalid_stop_orders_at', status=400)
        stall.current_session = session
        stall.save(update_fields=['current_session', 'transaction_enabled'])
        audit(request.user, 'stall_status', stall.id, status=data['status'], confirmed=data['confirm_location'])
    return Response(StallSerializer(stall, context={**stall_context(request), 'merchant': True}).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def merchant_order_action(request, order_id):
    action = serializers.ChoiceField(choices=['accept', 'update_prep', 'reject', 'ready', 'confirm_payment', 'complete', 'approve_cancel', 'deny_cancel', 'dispatch', 'arrive', 'report_delivery_issue', 'resolve_delivery_issue']).run_validation(request.data.get('action'))
    code = serializers.CharField(max_length=8, allow_blank=True).run_validation(request.data.get('pickup_code', ''))
    reason = serializers.CharField(max_length=200, allow_blank=True).run_validation(request.data.get('reason', ''))
    options = {}
    if action in ('accept', 'update_prep'):
        if 'prep_minutes' in request.data:
            options['prep_minutes'] = serializers.IntegerField(min_value=1, max_value=180).run_validation(request.data['prep_minutes'])
        if 'idempotency_key' in request.data:
            options['idempotency_key'] = serializers.CharField(min_length=8, max_length=128).run_validation(request.data['idempotency_key'])
    return Response(OrderSerializer(merchant_action(order_id, request.user, action, code, reason, **options), context={'merchant': True, 'request': request}).data)


@api_view(['GET'])
@throttle_classes([])
def health(request):
    try:
        with connection.cursor() as cursor: cursor.execute('SELECT 1')
        return Response({'status': 'ok'})
    except Exception:
        return Response({'status': 'unavailable'}, status=503)
