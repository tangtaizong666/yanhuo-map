import math
from django.utils import timezone
from django.db.models import Avg
from rest_framework import serializers
from .models import Area, Product, Order, OrderItem, PaymentAttempt, Review, Stall, Follow, SiteConfiguration
from .tastes import PortionsField


def public_payment_readiness(stall, context):
    from .payments import readiness
    from .simulation import eligible, payment_readiness
    if eligible(stall):
        return {key: value for key, value in payment_readiness(stall).items() if key != 'account_key'}
    if stall.is_demo:
        return {'mode': 'live', 'available': False, 'reason': '示例摊位不发起真实微信扣款，当前支持到摊付款。', 'channels': []}
    cache = context.setdefault('_payment_readiness', {})
    if stall.merchant_id not in cache:
        available = readiness(stall.merchant)
        cache[stall.merchant_id] = {'mode': 'live', **{key: available[key] for key in ('available', 'reason', 'channels')}}
    return cache[stall.merchant_id]


def user_data(user):
    if not user or not user.is_authenticated: return None
    return {'id': user.id, 'username': user.username, 'display_name': user.first_name or user.username,
        'is_merchant': hasattr(user, 'merchant_profile'), 'is_staff': user.is_staff,
        'merchant_application_status': getattr(getattr(user, 'merchant_application', None), 'status', None)}


class AreaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Area
        fields = ['id', 'name', 'subtitle', 'latitude', 'longitude']


class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ['id', 'name', 'description', 'category', 'image', 'price_cents', 'stock', 'stock_version', 'sale_paused', 'is_active', 'taste_options']


class ReviewSerializer(serializers.ModelSerializer):
    display_name = serializers.SerializerMethodField()
    class Meta:
        model = Review
        fields = ['id', 'rating', 'content', 'created_at', 'display_name', 'merchant_reply', 'replied_at']
    def get_display_name(self, obj):
        name = obj.user.first_name or obj.user.username
        return name[:1] + '同学'


class StallSerializer(serializers.ModelSerializer):
    order_unavailable_reason = serializers.SerializerMethodField()
    transaction_enabled = serializers.SerializerMethodField()
    address = serializers.CharField(source='location.address', read_only=True, default='位置尚未确认')
    latitude = serializers.FloatField(source='location.latitude', read_only=True, default=None)
    longitude = serializers.FloatField(source='location.longitude', read_only=True, default=None)
    area_name = serializers.CharField(source='area.name', read_only=True)
    status = serializers.SerializerMethodField()
    session_status = serializers.CharField(source='current_session.status', read_only=True, default='closed')
    business_session_id = serializers.IntegerField(source='current_session_id', read_only=True, allow_null=True)
    last_confirmed_at = serializers.DateTimeField(source='current_session.last_confirmed_at', read_only=True, default=None)
    closes_at = serializers.DateTimeField(source='current_session.closes_at', read_only=True, default=None)
    stop_orders_at = serializers.DateTimeField(source='current_session.stop_orders_at', read_only=True, default=None)
    prep_active_orders = serializers.IntegerField(read_only=True)
    receiving_status = serializers.CharField(read_only=True)
    receiving_age_seconds = serializers.SerializerMethodField()
    can_order = serializers.SerializerMethodField()
    qualification_note = serializers.CharField(source='merchant.qualification_note', read_only=True)
    merchant_name = serializers.CharField(source='merchant.business_name', read_only=True)
    contact_phone = serializers.CharField(source='merchant.contact_phone', read_only=True)
    rating = serializers.SerializerMethodField()
    review_count = serializers.SerializerMethodField()
    order_count = serializers.SerializerMethodField()
    distance_m = serializers.SerializerMethodField()
    is_followed = serializers.SerializerMethodField()
    products = serializers.SerializerMethodField()
    reviews = serializers.SerializerMethodField()
    wechat_payment = serializers.SerializerMethodField()
    delivery = serializers.SerializerMethodField()
    services = serializers.SerializerMethodField()
    class Meta:
        model = Stall
        fields = ['id', 'name', 'description', 'category', 'image', 'address', 'latitude', 'longitude',
          'area_id', 'area_name', 'status', 'session_status', 'business_session_id', 'last_confirmed_at', 'closes_at', 'prep_minutes',
          'transaction_enabled', 'can_order', 'qualification_note', 'merchant_name', 'contact_phone',
          'rating', 'review_count', 'order_count', 'distance_m', 'is_followed', 'products', 'reviews', 'wechat_payment', 'delivery', 'services',
          'arrival_note', 'arrival_image', 'accepting_orders', 'order_unavailable_reason', 'location_draft_address', 'usual_hours',
          'prep_capacity', 'prep_active_orders', 'stop_orders_at', 'receiving_seen_at', 'receiving_status', 'receiving_age_seconds']
    def to_representation(self, instance):
        result = super().to_representation(instance)
        if not self.context.get('merchant'): result.pop('location_draft_address', None)
        return result
    def get_order_unavailable_reason(self, obj): return obj.order_unavailable_reason(self.context.get('config'))
    def get_receiving_age_seconds(self, obj):
        if obj.receiving_seen_at is None: return None
        return max(0, (timezone.now() - obj.receiving_seen_at).total_seconds())
    def get_services(self, obj):
        if not self.context.get('merchant'): return None
        from .merchant import service_settings
        return service_settings(obj)
    def get_delivery(self, obj):
        from .delivery import delivery_settings
        return delivery_settings(obj, context=self.context)
    def get_wechat_payment(self, obj): return public_payment_readiness(obj, self.context)
    def get_status(self, obj): return obj.effective_status(self.context.get('config'))
    def get_transaction_enabled(self, obj): return obj.transaction_enabled and obj.merchant.is_verified
    def get_can_order(self, obj): return obj.can_order(self.context.get('config'))
    def get_rating(self, obj):
        if hasattr(obj, 'rating_average'):
            return round(obj.rating_average, 1) if obj.rating_average is not None else None
        ratings = [r.rating for r in obj.reviews.all()]
        return round(sum(ratings) / len(ratings), 1) if ratings else None
    def get_review_count(self, obj):
        return obj.rating_count if hasattr(obj, 'rating_count') else len(obj.reviews.all())
    def get_order_count(self, obj): return getattr(obj, 'completed_count', 0)
    def get_distance_m(self, obj):
        if hasattr(obj, 'distance_m_db'):
            return round(obj.distance_m_db) if obj.distance_m_db is not None else None
        point = self.context.get('point')
        if not point or not hasattr(obj, 'location'): return None
        lat1, lon1, lat2, lon2 = map(math.radians, (*point, obj.location.latitude, obj.location.longitude))
        value = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
        return round(6371000 * 2 * math.asin(min(1, math.sqrt(value))))
    def get_is_followed(self, obj): return obj.id in self.context.get('follow_ids', set())
    def get_products(self, obj):
        return ProductSerializer([p for p in obj.products.all() if p.is_active or self.context.get('merchant')], many=True).data
    def get_reviews(self, obj):
        rows = obj._preview_reviews if hasattr(obj, '_preview_reviews') else list(obj.reviews.all())[:20]
        return ReviewSerializer(rows, many=True).data


class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = ['product_id', 'name', 'image', 'unit_price_cents', 'quantity', 'portions']


class OrderSerializer(serializers.ModelSerializer):
    merchant_contact_phone = serializers.CharField(source='stall.merchant.contact_phone', read_only=True, default='')
    items_total_cents = serializers.SerializerMethodField()
    items = OrderItemSerializer(many=True, read_only=True)
    review = ReviewSerializer(read_only=True, default=None)
    current_address = serializers.CharField(source='stall.location.address', read_only=True, default='')
    location_changed = serializers.SerializerMethodField()
    wechat_payment = serializers.SerializerMethodField()
    payment = serializers.SerializerMethodField()
    payment_can_close = serializers.SerializerMethodField()
    refund = serializers.SerializerMethodField()
    refunds = serializers.SerializerMethodField()
    financial_hold_reason = serializers.SerializerMethodField()
    allowed_actions = serializers.SerializerMethodField()
    class Meta:
        model = Order
        fields = ['id', 'mode', 'number', 'stall_id', 'stall_name', 'stall_image', 'status', 'payment_status',
            'payment_method', 'payment_review_required', 'wechat_payment', 'payment', 'payment_can_close', 'refund',
            'refunds', 'financial_hold_reason', 'allowed_actions',
            'total_cents', 'created_at', 'accepted_at', 'ready_at', 'completed_at', 'paid_at', 'expires_at', 'pickup_code',
            'estimated_ready_at', 'prep_updated_at', 'prep_delay_reason',
            'pickup_address', 'pickup_latitude', 'pickup_longitude', 'current_address', 'location_changed',
            'note', 'contact_phone', 'merchant_contact_phone', 'cancel_requested', 'cancel_reason', 'review', 'items',
            'fulfillment_type', 'items_total_cents', 'delivery_fee_cents', 'delivery_point_id',
            'delivery_point_name', 'delivery_point_address', 'delivery_point_latitude', 'delivery_point_longitude',
            'recipient_name', 'delivery_eta_min_at', 'delivery_eta_max_at', 'dispatched_at', 'arrived_at', 'delivery_issue']
    def get_items_total_cents(self, obj): return obj.total_cents - obj.delivery_fee_cents
    def get_location_changed(self, obj):
        loc = getattr(obj.stall, 'location', None)
        return bool(loc and (loc.address != obj.pickup_address or
            abs(loc.latitude-obj.pickup_latitude) > 0.00001 or abs(loc.longitude-obj.pickup_longitude) > 0.00001))
    def get_wechat_payment(self, obj):
        ready = public_payment_readiness(obj.stall, self.context)
        if ready['mode'] != obj.mode:
            return {'mode': obj.mode, 'available': False, 'channels': [], 'reason': '此订单保留原有服务模式，当前不可发起新的线上支付。'}
        return ready
    def _payment(self, obj):
        return next(iter(obj.payments.all()), None)
    def get_payment(self, obj):
        payment = self._payment(obj)
        if payment is None: return None
        show_entry = (payment.status == 'pending' and payment.expires_at > timezone.now()
            and not self.context.get('merchant') and 'pay' in self.get_allowed_actions(obj))
        return {'id': str(payment.pk), 'mode': payment.mode, 'status': payment.status, 'channel': payment.channel,
            'code_url': payment.code_url if show_entry else '', 'h5_url': payment.h5_url if show_entry else '',
            'expires_at': payment.expires_at.isoformat(),
            'error_message': '订单存在支付异常，请联系商家与运营核对。' if obj.payment_review_required else payment.error_message}
    def get_payment_can_close(self, obj):
        return 'close_payment' in self.get_allowed_actions(obj)
    def get_refund(self, obj):
        refund = getattr(obj, 'payment_refund', None)
        if refund is None: return None
        return self._refund_data(refund)
    @staticmethod
    def _refund_data(refund):
        return {'id': str(refund.pk), 'mode': refund.mode, 'status': refund.status, 'reason': refund.reason,
            'amount_cents': refund.amount_cents, 'created_at': refund.created_at.isoformat(),
            'completed_at': refund.completed_at.isoformat() if refund.completed_at else None,
            'resolved_at': refund.resolved_at.isoformat() if refund.resolved_at else None,
            'source': refund.source, 'replaces_id': str(refund.replaces_id) if refund.replaces_id else None,
            'error_message': refund.error_message}
    def get_refunds(self, obj):
        return [self._refund_data(refund) for refund in obj.refunds.all()]
    def get_financial_hold_reason(self, obj):
        from .financial import hold_reason
        return hold_reason(obj)
    def get_allowed_actions(self, obj):
        from .financial import allowed_actions
        from .permissions import can_operate_all
        merchant = bool(self.context.get('merchant'))
        actions = allowed_actions(obj, merchant=merchant)
        request = self.context.get('request')
        if request is None:
            return actions
        actor = request.user
        if not actor.is_authenticated or not actor.is_active:
            return []
        if not merchant:
            return actions if obj.user_id == actor.pk else []
        owns_stall = obj.stall.merchant.user_id == actor.pk
        can_change = owns_stall or can_operate_all(actor, 'market.change_order')
        can_refund = owns_stall or can_operate_all(actor, 'market.add_paymentrefund')
        refund_actions = {'refund', 'simulate_refund_success', 'simulate_refund_failure', 'simulate_refund_pending'}
        # The merchant's sync button calls the refund endpoint, which requires
        # refund permission. Ordinary refresh remains a separate read-only GET.
        return [action for action in actions if
            (can_refund and bool(obj.refunds.all()) if action == 'sync_payment' else
             can_refund if action in refund_actions else can_change)]
    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get('request')
        if (self.context.get('merchant') or (request is not None and request.user.pk != instance.user_id)
                or data['financial_hold_reason'] or instance.cancel_requested
                or instance.delivery_issue or instance.payment_status == 'refunded' or instance.status not in ('ready', 'arrived')):
            data['pickup_code'] = ''
        return data


class CartItemInput(serializers.Serializer):
    product_id = serializers.IntegerField(min_value=1)
    quantity = serializers.IntegerField(min_value=1, max_value=99)
    expected_price_cents = serializers.IntegerField(min_value=1, max_value=1000000)
    portions = PortionsField(required=False)
    def validate(self, attrs):
        if 'portions' in attrs and len(attrs['portions']) != attrs['quantity']:
            raise serializers.ValidationError({'portions': '口味份数必须与购买数量一致。'})
        return attrs


class OrderInput(serializers.Serializer):
    fulfillment_type = serializers.ChoiceField(choices=['pickup', 'delivery'], default='pickup')
    delivery_point_id = serializers.IntegerField(min_value=1, required=False)
    expected_delivery_fee_cents = serializers.IntegerField(min_value=0, max_value=100000, required=False)
    recipient_name = serializers.CharField(max_length=30, allow_blank=True, default='')
    stall_id = serializers.IntegerField(min_value=1)
    items = CartItemInput(many=True, min_length=1, max_length=50)
    note = serializers.CharField(max_length=200, required=False, allow_blank=True, default='')
    contact_phone = serializers.RegexField(r'^[0-9+() \-]{0,30}$', required=False, allow_blank=True, default='')
    idempotency_key = serializers.CharField(min_length=8, max_length=128)
    def validate(self, attrs):
        if attrs['fulfillment_type'] == 'delivery':
            for field in ('delivery_point_id', 'expected_delivery_fee_cents', 'recipient_name', 'contact_phone'):
                if field not in attrs or attrs[field] == '':
                    raise serializers.ValidationError({field: '配送订单需要此信息。'})
            digits = ''.join(c for c in attrs['contact_phone'] if c.isdigit())
            if not 7 <= len(digits) <= 15: raise serializers.ValidationError({'contact_phone': '请填写可联系的电话号码。'})
        return attrs
    def validate_items(self, items):
        ids = [i['product_id'] for i in items]
        if len(ids) != len(set(ids)): raise serializers.ValidationError('商品不可重复，请合并数量。')
        return items


class RegistrationInput(serializers.Serializer):
    username = serializers.RegexField(r'^[a-zA-Z0-9_]{3,30}$')
    password = serializers.CharField(min_length=8, max_length=128, write_only=True)
    display_name = serializers.CharField(max_length=30, required=False, allow_blank=True)


class StallCutoffInput(serializers.Serializer):
    cutoff_only = serializers.BooleanField()
    expected_session_id = serializers.IntegerField(min_value=1)
    stop_orders_at = serializers.DateTimeField(allow_null=True)

    def validate(self, attrs):
        if not attrs['cutoff_only']:
            raise serializers.ValidationError('仅调整本场截止时 cutoff_only 必须为 true。')
        if set(self.initial_data) - set(self.fields):
            raise serializers.ValidationError('调整本场截止不能同时修改营业状态、位置或其他资料。')
        return attrs


class StallStatusInput(serializers.Serializer):
    status = serializers.ChoiceField(choices=['open', 'paused', 'closed'])
    confirm_location = serializers.BooleanField(default=False)
    latitude = serializers.FloatField(min_value=-90, max_value=90, required=False)
    longitude = serializers.FloatField(min_value=-180, max_value=180, required=False)
    address = serializers.CharField(max_length=200, required=False)
    closes_at = serializers.DateTimeField(required=False, allow_null=True)
    stop_orders_at = serializers.DateTimeField(required=False, allow_null=True)
    def validate(self, attrs):
        if ('latitude' in attrs) != ('longitude' in attrs):
            raise serializers.ValidationError('经纬度必须同时提供。')
        for field in ['latitude', 'longitude']:
            if field in attrs and not math.isfinite(attrs[field]):
                raise serializers.ValidationError('坐标必须是有效数字。')
        return attrs
