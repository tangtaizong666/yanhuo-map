import secrets
import uuid
import math
from datetime import time, timedelta
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone


class SiteConfiguration(models.Model):
    stale_minutes = models.PositiveIntegerField('位置过期分钟数', default=60)
    pending_minutes = models.PositiveIntegerField('接单超时分钟数', default=5)
    class Meta:
        verbose_name = verbose_name_plural = '运营参数'
    def save(self, *args, **kwargs):
        self.pk = 1
        self.stale_minutes = max(1, min(self.stale_minutes, 1440))
        self.pending_minutes = max(1, min(self.pending_minutes, 60))
        super().save(*args, **kwargs)
    @classmethod
    def current(cls):
        return cls.objects.filter(pk=1).first() or cls(stale_minutes=60, pending_minutes=5)


class Area(models.Model):
    name = models.CharField('名称', max_length=80)
    subtitle = models.CharField('描述', max_length=160, blank=True)
    latitude = models.FloatField()
    longitude = models.FloatField()
    is_demo = models.BooleanField(default=False)
    class Meta:
        verbose_name = verbose_name_plural = '校园区域'
    def __str__(self): return self.name


class MerchantProfile(models.Model):
    # Online-food rules require a physical storefront matching the licensed address.
    # Mobile vendors can publish information; online reservations also require admission.
    TIERS = [('mobile_vendor', '流动摊位（找摊与信息展示）'), ('storefront', '实体门店（经核验可开通线上交易）')]
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='merchant_profile')
    business_name = models.CharField('经营主体', max_length=120)
    contact_phone = models.CharField('公开联系电话', max_length=30, blank=True)
    qualification_note = models.TextField('经营资质公示', blank=True)
    is_verified = models.BooleanField('资质已核验', default=False)
    license_number = models.CharField('许可证号', max_length=100, blank=True)
    qualification_tier = models.CharField('经营资质类型', max_length=20, choices=TIERS, default='mobile_vendor',
        help_text='公开展示不等于交易准入；只有经营资格核验有效并获运营授权的实体门店才开放线上下单、支付与配送。')
    licensed_business_address = models.CharField('证照载明经营场所', max_length=200, blank=True)
    food_preparation_address = models.CharField('实际加工制作地址', max_length=200, blank=True)
    license_valid_until = models.DateField('许可证有效期至', null=True, blank=True)
    wechat_pay_account = models.CharField('独立微信收款配置标识', max_length=64, null=True, blank=True, unique=True,
        help_text='仅绑定该经营主体自有商户号；密钥由部署配置管理。')
    def save(self, *args, **kwargs):
        self.wechat_pay_account = self.wechat_pay_account or None
        super().save(*args, **kwargs)
    class Meta:
        verbose_name = verbose_name_plural = '商户档案'
        constraints = [models.CheckConstraint(name='storefront_requires_addresses', condition=Q(qualification_tier='mobile_vendor')
            | (Q(qualification_tier='storefront') & ~Q(licensed_business_address='') & ~Q(food_preparation_address='')))]
    def __str__(self): return self.business_name
    def clean(self):
        super().clean()
        if self.qualification_tier == 'storefront':
            from .admission import has_admission_text
            missing = {name: '实体门店需填写此项。' for name in ('licensed_business_address', 'food_preparation_address')
                if not has_admission_text(getattr(self, name))}
            if self.is_verified:
                if not has_admission_text(self.license_number):
                    missing['license_number'] = '核验通过的实体门店需填写经营许可证号。'
                if self.license_valid_until is None:
                    missing['license_valid_until'] = '核验通过的实体门店需填写已核验的许可证有效期。'
                elif self.license_valid_until < timezone.localdate():
                    missing['license_valid_until'] = '经营许可证已过有效期，不能标记资质核验通过。'
            if missing: raise ValidationError(missing)
    def online_trade_reason(self):
        from .admission import merchant_trade_reason
        return merchant_trade_reason(self)


class DeliveryPoint(models.Model):
    is_simulation = models.BooleanField('仅模拟交接点', default=False)
    area = models.ForeignKey(Area, on_delete=models.PROTECT, related_name='delivery_points')
    name = models.CharField('交接点名称', max_length=80)
    address = models.CharField('交接位置说明', max_length=200)
    latitude = models.FloatField()
    longitude = models.FloatField()
    is_active = models.BooleanField('已获准开放交接', default=False)
    class Meta:
        verbose_name = verbose_name_plural = '配送交接点'
        constraints = [models.UniqueConstraint(fields=['area'], condition=Q(is_simulation=True), name='one_simulation_point_per_area')]
    def __str__(self): return f'{self.area.name} / {self.name}'
    def clean(self):
        super().clean()
        for field, limit in [('latitude', 90), ('longitude', 180)]:
            value = getattr(self, field)
            if value is None or not math.isfinite(value) or not -limit <= value <= limit:
                raise ValidationError({field: '请输入有效范围内的经纬度。'})


class Stall(models.Model):
    public_phone_enabled = models.BooleanField('允许公开联系电话', default=False)
    arrival_note = models.CharField('认摊说明', max_length=200, blank=True)
    arrival_image = models.CharField('认摊现场照片', max_length=500, blank=True)
    # The vendor's own WeChat/Alipay personal QR; the platform only displays it and never touches the money.
    payment_qr_image = models.CharField('摊主自有收款码', max_length=500, blank=True)
    location_draft_address = models.CharField('待核验位置草稿', max_length=200, blank=True)
    accepting_orders = models.BooleanField('接收新订单', default=True)
    prep_capacity = models.PositiveSmallIntegerField('同时备餐订单上限', null=True, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(100)])
    receiving_seen_at = models.DateTimeField('接单页最近成功同步', null=True, blank=True)
    usual_hours = models.CharField('通常出摊时段（计划）', max_length=100, blank=True)
    simulation_payment_enabled = models.BooleanField('模拟线上支付', default=False)
    simulation_delivery_enabled = models.BooleanField('模拟外卖', default=False)
    simulation_defaults_prepared = models.BooleanField(default=False)
    merchant = models.ForeignKey(MerchantProfile, on_delete=models.PROTECT, related_name='stalls')
    area = models.ForeignKey(Area, on_delete=models.PROTECT, related_name='stalls')
    name = models.CharField('摊位名称', max_length=80)
    description = models.TextField('描述', blank=True)
    category = models.CharField('品类', max_length=30)
    image = models.CharField('图片地址', max_length=500, blank=True)
    prep_minutes = models.PositiveIntegerField('预计备餐分钟', default=10)
    transaction_enabled = models.BooleanField('允许在线接单', default=False)
    current_session = models.ForeignKey('BusinessSession', null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    is_demo = models.BooleanField(default=False)
    is_visible = models.BooleanField('对用户显示', default=True)
    delivery_enabled = models.BooleanField('商家开启配送', default=False)
    delivery_approved = models.BooleanField('运营配送准入通过', default=False)
    delivery_fee_cents = models.PositiveIntegerField('配送费（分）', default=300)
    delivery_min_order_cents = models.PositiveIntegerField('起送餐费（分）', default=1500)
    delivery_eta_min_minutes = models.PositiveIntegerField('预计送达下限分钟', default=25)
    delivery_eta_max_minutes = models.PositiveIntegerField('预计送达上限分钟', default=45)
    delivery_starts_at = models.TimeField('配送开始时间', default=time(11, 0))
    delivery_ends_at = models.TimeField('配送结束时间', default=time(14, 0))
    delivery_capacity = models.PositiveIntegerField('同时进行配送订单上限', default=5)
    delivery_points = models.ManyToManyField(DeliveryPoint, blank=True, related_name='stalls')
    class Meta:
        verbose_name = verbose_name_plural = '摊位'
        constraints = [models.CheckConstraint(condition=Q(prep_capacity__isnull=True) | Q(prep_capacity__gte=1, prep_capacity__lte=100), name='valid_prep_capacity')]
    def __str__(self): return self.name
    def effective_status(self, config=None):
        session = self.current_session
        if not session or session.status == 'closed': return 'closed'
        now = timezone.now()
        if session.closes_at and now >= session.closes_at: return 'closed'
        minutes = (config or SiteConfiguration.current()).stale_minutes
        if not session.last_confirmed_at or session.last_confirmed_at < now - timedelta(minutes=minutes): return 'stale'
        return session.status
    def can_order(self, config=None, *, existing_order=False):
        return not self.order_unavailable_reason(config, existing_order=existing_order)
    def prep_active_orders(self):
        count = getattr(self, 'prep_active_count', None)
        return count if count is not None else self.orders.filter(status__in=('pending_payment', 'pending', 'preparing')).count()
    def new_order_gate_code(self):
        session = self.current_session
        if session and session.stop_orders_at and timezone.now() >= session.stop_orders_at:
            return 'ordering_stopped'
        if self.prep_capacity is not None and self.prep_active_orders() >= self.prep_capacity:
            return 'prep_capacity_reached'
        return ''
    def receiving_status(self):
        if self.receiving_seen_at is None: return 'unknown'
        return 'recent' if self.receiving_seen_at >= timezone.now()-timedelta(seconds=90) else 'stale'
    def order_unavailable_reason(self, config=None, *, existing_order=False):
        from .admission import pickup_unavailable_reason
        return pickup_unavailable_reason(self, config, existing_order=existing_order)


class StallLocation(models.Model):
    stall = models.OneToOneField(Stall, on_delete=models.CASCADE, related_name='location')
    address = models.CharField('取餐位置', max_length=200)
    latitude = models.FloatField()
    longitude = models.FloatField()
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        verbose_name = verbose_name_plural = '摊位位置'


class BusinessSession(models.Model):
    STATUSES = [('open', '出摊中'), ('paused', '暂歇'), ('closed', '已收摊')]
    stall = models.ForeignKey(Stall, on_delete=models.CASCADE, related_name='sessions')
    status = models.CharField(max_length=10, choices=STATUSES, default='open')
    opened_at = models.DateTimeField(default=timezone.now)
    last_confirmed_at = models.DateTimeField(default=timezone.now)
    closes_at = models.DateTimeField(null=True, blank=True)
    stop_orders_at = models.DateTimeField('本场停止线上接单时间', null=True, blank=True)
    class Meta:
        verbose_name = verbose_name_plural = '营业会话'


class Product(models.Model):
    display_availability = models.CharField('找摊展示供应状态', max_length=12, blank=True, default='',
        choices=[('', '沿用原供应状态'), ('available', '今天有'), ('sold_out', '卖完了'), ('paused', '暂时不卖')])
    sale_paused = models.BooleanField('暂停供应', default=False)
    stock_version = models.PositiveBigIntegerField('库存版本', default=0, editable=False)
    taste_options = models.JSONField('免费口味选项', default=list, blank=True)
    stall = models.ForeignKey(Stall, on_delete=models.CASCADE, related_name='products')
    name = models.CharField('商品名称', max_length=80)
    description = models.CharField('商品描述', max_length=200, blank=True)
    category = models.CharField('菜单分类', max_length=30, blank=True, default='招牌美味')
    image = models.CharField('图片地址', max_length=500, blank=True)
    price_cents = models.PositiveIntegerField('价格（分）')
    stock = models.PositiveIntegerField('库存', default=0)
    is_active = models.BooleanField(default=True)
    class Meta:
        verbose_name = verbose_name_plural = '商品'
        constraints = [models.CheckConstraint(condition=Q(price_cents__gte=1), name='product_positive_price')]
    def __str__(self): return f'{self.stall.name} / {self.name}'
    def clean(self):
        super().clean()
        from .tastes import TasteOptionsField
        from rest_framework.exceptions import ValidationError as APIValidationError
        try: self.taste_options = TasteOptionsField().run_validation(self.taste_options)
        except APIValidationError as exc: raise ValidationError({'taste_options': str(exc.detail)}) from exc


class Follow(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    stall = models.ForeignKey(Stall, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'stall'], name='unique_follow')]


def order_number():
    return timezone.localtime().strftime('%Y%m%d%H%M%S') + secrets.token_hex(4).upper()


def pickup_code():
    return ''.join(secrets.choice('0123456789') for _ in range(8))


class Order(models.Model):
    mode = models.CharField(max_length=10, default='live', choices=[('live', '正式'), ('simulation', '模拟')])
    STATUSES = [('pending_payment', '待付款'), ('pending', '待接单'), ('preparing', '制作中'), ('ready', '待取餐'),
        ('delivering', '配送中'), ('arrived', '已到交接点'),
        ('completed', '已完成'), ('cancelled', '已取消'), ('rejected', '已拒单')]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    number = models.CharField(max_length=40, unique=True, default=order_number)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='orders')
    stall = models.ForeignKey(Stall, on_delete=models.PROTECT, related_name='orders')
    stall_name = models.CharField(max_length=80)
    stall_image = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=15, choices=STATUSES, default='pending', db_index=True)
    payment_status = models.CharField(max_length=10, default='unpaid', choices=[('unpaid', '未收款'), ('paid', '已收款'), ('refunding', '退款中'), ('refunded', '已退款')])
    payment_method = models.CharField(max_length=10, default='offline', choices=[('offline', '到摊付款'), ('wechat', '微信支付')])
    payment_review_required = models.BooleanField('支付异常待核对', default=False)
    total_cents = models.PositiveIntegerField()
    fulfillment_type = models.CharField(max_length=10, default='pickup', choices=[('pickup', '到摊自取'), ('delivery', '商家配送')])
    delivery_fee_cents = models.PositiveIntegerField(default=0)
    delivery_point = models.ForeignKey(DeliveryPoint, null=True, blank=True, on_delete=models.PROTECT)
    delivery_point_name = models.CharField(max_length=80, blank=True)
    delivery_point_address = models.CharField(max_length=200, blank=True)
    delivery_point_latitude = models.FloatField(null=True, blank=True)
    delivery_point_longitude = models.FloatField(null=True, blank=True)
    recipient_name = models.CharField(max_length=30, blank=True)
    delivery_eta_min_at = models.DateTimeField(null=True, blank=True)
    delivery_eta_max_at = models.DateTimeField(null=True, blank=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    arrived_at = models.DateTimeField(null=True, blank=True)
    delivery_issue = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    estimated_ready_at = models.DateTimeField('商家预计出餐时间', null=True, blank=True)
    prep_updated_at = models.DateTimeField('备餐预估确认时间', null=True, blank=True)
    prep_delay_reason = models.CharField('备餐调整说明', max_length=200, blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(db_index=True)
    pickup_code = models.CharField(max_length=8, default=pickup_code)
    pickup_address = models.CharField(max_length=200)
    pickup_latitude = models.FloatField()
    pickup_longitude = models.FloatField()
    note = models.CharField(max_length=200, blank=True)
    contact_phone = models.CharField(max_length=30, blank=True)
    cancel_requested = models.BooleanField(default=False)
    cancel_reason = models.CharField(max_length=200, blank=True)
    inventory_released = models.BooleanField(default=False)
    idempotency_key = models.CharField(max_length=128)
    request_hash = models.CharField(max_length=64)
    @property
    def payment_refund(self):
        """Compatibility summary; the complete immutable-number history is refunds."""
        from .financial import current_refund
        refund = current_refund(self)
        if refund is None:
            raise AttributeError('This order has no refund.')
        return refund
    class Meta:
        verbose_name = verbose_name_plural = '订单'
        ordering = ['-created_at']
        indexes = [models.Index(fields=['user', '-created_at', '-id'], name='order_user_history_idx'),
            models.Index(fields=['stall', '-created_at', '-id'], name='order_stall_history_idx')]
        constraints = [models.UniqueConstraint(fields=['user', 'idempotency_key'], name='unique_order_request'),
            models.CheckConstraint(condition=Q(total_cents__gte=1), name='order_positive_amount')]


class OrderItem(models.Model):
    portions = models.JSONField('每份口味与备注快照', default=list, blank=True)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    name = models.CharField(max_length=80)
    image = models.CharField(max_length=500, blank=True)
    unit_price_cents = models.PositiveIntegerField()
    quantity = models.PositiveIntegerField()


class PreparationRequest(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='preparation_requests')
    idempotency_key = models.CharField(max_length=128)
    request_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['order', 'idempotency_key'], name='unique_preparation_request')]


class PaymentAttempt(models.Model):
    """A durable merchant payment intent. Unknown outcomes never release its hold."""
    ACTIVE_STATUSES = ('creating', 'pending', 'reconcile', 'review')
    mode = models.CharField(max_length=10, default='live', choices=[('live', '正式'), ('simulation', '模拟')])
    simulation_state = models.CharField(max_length=16, default='NOTPAY')
    simulation_settled_at = models.DateTimeField(null=True, blank=True)
    STATUSES = [('creating', '正在创建'), ('pending', '待支付'), ('paid', '已支付'),
        ('closed', '已关闭'), ('reconcile', '结果待确认'), ('review', '需人工核对')]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='payments')
    merchant = models.ForeignKey(MerchantProfile, on_delete=models.PROTECT, related_name='payments')
    account_key = models.CharField(max_length=64)
    mchid = models.CharField(max_length=32)
    appid = models.CharField(max_length=32)
    out_trade_no = models.CharField(max_length=32, unique=True)
    transaction_id = models.CharField(max_length=64, null=True, blank=True, unique=True)
    channel = models.CharField(max_length=10, choices=[('native', '微信扫码'), ('h5', '手机微信支付'), ('simulation', '模拟支付')])
    amount_cents = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default='CNY')
    status = models.CharField(max_length=12, choices=STATUSES, default='creating', db_index=True)
    code_url = models.CharField(max_length=4096, blank=True)
    h5_url = models.CharField(max_length=4096, blank=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    next_query_at = models.DateTimeField(null=True, blank=True)
    consecutive_query_failures = models.PositiveSmallIntegerField(default=0)
    request_in_flight_until = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=80, blank=True)
    error_message = models.CharField(max_length=200, blank=True)
    class Meta:
        verbose_name = verbose_name_plural = '微信支付记录'
        ordering = ['-created_at']
        constraints = [models.CheckConstraint(condition=Q(amount_cents__gte=1), name='payment_positive_amount'),
            models.UniqueConstraint(fields=['order'], condition=Q(status__in=['creating', 'pending', 'reconcile', 'review']), name='one_active_payment_per_order')]


class PaymentRefund(models.Model):
    ACTIVE_STATUSES = ('creating', 'processing', 'reconcile', 'abnormal')
    mode = models.CharField(max_length=10, default='live', choices=[('live', '正式'), ('simulation', '模拟')])
    simulation_state = models.CharField(max_length=16, default='SUCCESS')
    simulation_settled_at = models.DateTimeField(null=True, blank=True)
    STATUSES = [('creating', '正在申请'), ('processing', '退款处理中'), ('success', '已退款'),
        ('closed', '退款关闭'), ('abnormal', '退款异常'), ('reconcile', '结果待确认')]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payment = models.ForeignKey(PaymentAttempt, on_delete=models.PROTECT, related_name='refunds')
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name='refunds')
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)
    request_in_flight_until = models.DateTimeField(null=True, blank=True)
    mchid = models.CharField('原收款商户号', max_length=32)
    out_refund_no = models.CharField(max_length=64)
    refund_id = models.CharField(max_length=64, null=True, blank=True, unique=True)
    amount_cents = models.PositiveIntegerField()
    reason = models.CharField(max_length=80)
    status = models.CharField(max_length=12, choices=STATUSES, default='creating', db_index=True)
    created_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    next_query_at = models.DateTimeField(null=True, blank=True)
    consecutive_query_failures = models.PositiveSmallIntegerField(default=0)
    resolved_at = models.DateTimeField('核验结案时间', null=True, blank=True)
    replaces = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='retries')
    source = models.CharField(max_length=16, default='application', choices=[('application', '订单申请'), ('retry', '运营重试'), ('external', '外部退款核验'), ('compensation', '异常付款补偿')])
    operation_key = models.CharField(max_length=128, null=True, blank=True, unique=True)
    operation_hash = models.CharField(max_length=64, blank=True)
    error_code = models.CharField(max_length=80, blank=True)
    error_message = models.CharField(max_length=200, blank=True)
    def save(self, *args, **kwargs):
        if self._state.adding:
            if self.mchid and self.mchid != self.payment.mchid:
                raise ValidationError('退款收款主体必须与原付款相同。')
            self.mchid = self.payment.mchid
        return super().save(*args, **kwargs)
    class Meta:
        verbose_name = verbose_name_plural = '微信退款记录'
        ordering = ['-created_at', '-id']
        constraints = [models.CheckConstraint(condition=Q(amount_cents__gte=1), name='refund_positive_amount'),
            models.UniqueConstraint(fields=['mchid', 'out_refund_no'], name='unique_merchant_refund_number'),
            models.UniqueConstraint(fields=['payment'], condition=Q(status__in=['creating', 'processing', 'reconcile', 'abnormal']), name='one_active_refund_per_payment')]


class Review(models.Model):
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name='review')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    stall = models.ForeignKey(Stall, on_delete=models.PROTECT, related_name='reviews')
    rating = models.PositiveSmallIntegerField()
    content = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)
    merchant_reply = models.CharField('商家回复', max_length=500, blank=True)
    replied_at = models.DateTimeField('回复时间', null=True, blank=True)
    class Meta:
        verbose_name = verbose_name_plural = '评价'
        constraints = [models.CheckConstraint(condition=Q(rating__gte=1) & Q(rating__lte=5), name='review_valid_rating')]


class Feedback(models.Model):
    verification = models.CharField('位置反馈核实结果', max_length=16, default='unreviewed',
        choices=[('unreviewed', '尚未核实'), ('confirmed', '已核实属实'), ('dismissed', '核实未成立')])
    verification_note = models.CharField('运营核实记录（不公开）', max_length=500, blank=True)
    order = models.ForeignKey(Order, null=True, blank=True, on_delete=models.PROTECT, related_name='help_reports')
    KINDS = [('general', '意见建议'), ('not_found', '没找到摊位'), ('wrong_location', '位置不对'), ('mismatch', '信息不符')]
    kind = models.CharField(max_length=20, choices=KINDS, default='general')
    stall = models.ForeignKey(Stall, null=True, blank=True, on_delete=models.SET_NULL, related_name='location_reports')
    location_snapshot = models.JSONField('用户看到的位置记录（未经核实）', default=dict, blank=True)
    dedupe_key = models.CharField(max_length=64, null=True, blank=True, unique=True)
    request_hash = models.CharField(max_length=64, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    content = models.CharField(max_length=1000)
    contact = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved = models.BooleanField(default=False)
    class Meta:
        verbose_name = verbose_name_plural = '意见反馈'


class MerchantApplication(models.Model):
    STATUSES = [('draft', '草稿'), ('submitted', '待审核'), ('needs_changes', '待补充'), ('approved', '已批准建档'), ('rejected', '未通过')]
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='merchant_application')
    source = models.CharField(max_length=10, choices=[('self', '自主填写'), ('assisted', '运营代录')], default='self')
    status = models.CharField(max_length=20, choices=STATUSES, default='draft', db_index=True)
    business_name = models.CharField('经营主体', max_length=120, blank=True)
    stall_name = models.CharField('摊位名称', max_length=80, blank=True)
    contact_phone = models.CharField('联系电话', max_length=30, blank=True)
    area = models.ForeignKey(Area, null=True, blank=True, on_delete=models.PROTECT)
    category = models.CharField('品类', max_length=30, blank=True)
    address_note = models.CharField('位置说明（待核验）', max_length=200, blank=True)
    description = models.CharField('经营介绍', max_length=1000, blank=True)
    review_note = models.CharField('审核意见', max_length=1000, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    approved_stall = models.OneToOneField(Stall, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField('本人确认资料时间', null=True, blank=True)
    class Meta:
        verbose_name = verbose_name_plural = '商家入驻申请'


class RestockBatch(models.Model):
    stall = models.ForeignKey(Stall, on_delete=models.PROTECT, related_name='restock_batches')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    idempotency_key = models.CharField(max_length=128)
    request_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['stall', 'idempotency_key'], name='unique_stall_restock_key')]


class StockCorrection(models.Model):
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name='stock_corrections')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    idempotency_key = models.CharField(max_length=128)
    request_hash = models.CharField(max_length=64)
    stock_before = models.PositiveIntegerField()
    stock_after = models.PositiveIntegerField()
    version_before = models.PositiveBigIntegerField()
    version_after = models.PositiveBigIntegerField()
    reason = models.CharField('盘点更正原因', max_length=200)
    created_at = models.DateTimeField(default=timezone.now)
    class Meta:
        verbose_name = verbose_name_plural = '线上可售余量更正记录'
        constraints = [models.UniqueConstraint(fields=['product', 'idempotency_key'], name='unique_stock_correction_key')]


class Event(models.Model):
    type = models.CharField(max_length=40, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    stall = models.ForeignKey(Stall, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)


class AuditLog(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=80)
    target = models.CharField(max_length=100)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        verbose_name = verbose_name_plural = '业务审计日志'


from .recovery_models import AccountRecovery  # noqa: E402,F401
from .security_models import AuthenticationFailureBucket, ProductCreation, MerchantOrderOperation  # noqa: E402,F401
from .operational_models import WorkerHeartbeat  # noqa: E402,F401
from .financial_models import FinancialEvidence  # noqa: E402,F401
from .notification_models import PaymentNotification  # noqa: E402,F401
