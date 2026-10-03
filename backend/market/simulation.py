"""Explicit local rehearsal provider. No credentials, HTTP, or actual movement of money.

The immutable mode on every order/payment/refund selects this provider. Turning
the rehearsal off never routes an old simulated transaction to WeChat.
"""
from datetime import time
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from .errors import BusinessError
from .models import DeliveryPoint, PaymentAttempt, PaymentRefund
from .wechatpay import GatewayError


def enabled():
    return bool(settings.SERVICES_SIMULATION_ENABLED and settings.DEMO_MODE and not settings.PRODUCTION)


def eligible(stall):
    return enabled() and stall.is_demo


def require(stall):
    if not eligible(stall):
        raise BusinessError('模拟服务已关闭或此摊位不属于示例环境。', 'simulation_unavailable', status=409)


def require_order_operation(order):
    if order.mode != 'simulation': return
    # Preserve the original offline pickup demonstration when the optional online
    # simulator is disabled. An online attempt or delivery always needs the flag.
    basic_cash_demo = (settings.DEMO_MODE and not settings.PRODUCTION and order.stall.is_demo
        and order.fulfillment_type == 'pickup' and order.payment_method == 'offline' and not order.payments.exists())
    if not basic_cash_demo: require(order.stall)


def payment_readiness(stall):
    available = eligible(stall) and stall.simulation_payment_enabled and stall.merchant.is_verified and stall.transaction_enabled
    return {'mode': 'simulation', 'available': available, 'channels': ['simulation'] if available else [],
        'reason': '模拟支付不会扣款，也不会向微信发送请求。' if available else '模拟线上支付暂未开启，请商家在经营服务中开启。',
        'account_key': f'simulation-{stall.merchant_id}'}


def prepare_stall(stall):
    """Idempotent defaults; deliberately does not verify a business or open it."""
    require(stall)
    point, _ = DeliveryPoint.objects.get_or_create(area=stall.area, is_simulation=True,
        defaults={'name': '模拟校园交接点', 'address': '仅供流程演练，不安排实际送货',
            'latitude': stall.area.latitude, 'longitude': stall.area.longitude, 'is_active': False})
    if not stall.simulation_defaults_prepared:
        names = ['delivery_fee_cents', 'delivery_min_order_cents', 'delivery_eta_min_minutes',
            'delivery_eta_max_minutes', 'delivery_starts_at', 'delivery_ends_at', 'delivery_capacity']
        untouched = not stall.delivery_points.exists() and all(getattr(stall, n) == stall._meta.get_field(n).get_default() for n in names)
        if untouched:
            stall.delivery_fee_cents, stall.delivery_min_order_cents = 200, 0
            stall.delivery_eta_min_minutes, stall.delivery_eta_max_minutes = 20, 40
            stall.delivery_starts_at, stall.delivery_ends_at = time.min, time(23, 59, 59)
            stall.delivery_capacity = 5
        stall.simulation_defaults_prepared = True
        stall.save(update_fields=names + ['simulation_defaults_prepared'] if untouched else ['simulation_defaults_prepared'])
        stall.delivery_points.add(point)
    return point


class SimulationClient:
    channels = ('simulation',)

    def __init__(self, payment):
        if payment.mode != 'simulation' or payment.order.mode != 'simulation' or not eligible(payment.order.stall):
            raise GatewayError('SIMULATION_DISABLED', '模拟服务已关闭，原模拟记录保留，不会转为真实支付。', outcome_unknown=False)
        self.payment = payment
        self.mchid, self.appid, self.enabled = payment.mchid, payment.appid, True

    def create_payment(self, **kwargs):
        return {}  # The explicit simulator panel replaces a QR/payment URL.

    def query_payment(self, number):
        payment = PaymentAttempt.objects.get(pk=self.payment.pk, out_trade_no=number, mode='simulation')
        if payment.simulation_state == 'UNKNOWN':
            raise GatewayError('SIMULATED_PENDING', '模拟结果待确认；未确认收款，可继续演练成功或失败。')
        data = {'appid': payment.appid, 'mchid': payment.mchid, 'out_trade_no': number,
            'trade_state': payment.simulation_state, 'amount': {'total': payment.amount_cents, 'currency': 'CNY'}}
        if payment.simulation_state == 'SUCCESS':
            data.update(transaction_id='SIM' + payment.pk.hex,
                success_time=payment.simulation_settled_at.isoformat())
        return data

    def close_payment(self, number):
        # Serialize with the explicit simulate action; a success racing closure
        # must force a fresh query instead of inventing a successful close.
        from .payments import _order
        with transaction.atomic():
            _order(self.payment.order_id)
            payment = PaymentAttempt.objects.get(pk=self.payment.pk, out_trade_no=number)
            if payment.simulation_state == 'SUCCESS':
                raise GatewayError('SIMULATED_ALREADY_PAID', '模拟付款已成功，请重新查询付款状态。')
            payment.simulation_state = 'CLOSED'
            payment.save(update_fields=['simulation_state'])
        return {}

    def query_refund(self, number):
        refund = PaymentRefund.objects.select_related('payment').get(payment=self.payment, out_refund_no=number, mode='simulation')
        data = {'mchid': self.payment.mchid, 'out_trade_no': self.payment.out_trade_no,
            'transaction_id': refund.payment.transaction_id, 'out_refund_no': number,
            'refund_id': 'SIMR' + refund.pk.hex, 'status': refund.simulation_state,
            'amount': {'total': self.payment.amount_cents, 'refund': refund.amount_cents, 'currency': 'CNY'}}
        if refund.simulation_state == 'SUCCESS':
            # Queueing a simulated refund authorizes this deterministic outcome;
            # retries keep the same identifier and completion timestamp.
            if refund.simulation_settled_at is None:
                PaymentRefund.objects.filter(pk=refund.pk, simulation_settled_at=None).update(simulation_settled_at=timezone.now())
                refund.refresh_from_db()
            data['success_time'] = refund.simulation_settled_at.isoformat()
        return data


def simulate_payment(order_id, user, payment_id, outcome):
    from .payments import _order, sync_payment
    from .services import audit
    with transaction.atomic():
        order = _order(order_id, user)
        require(order.stall)
        payment = order.payments.first()
        if order.mode != 'simulation' or not payment or payment.mode != 'simulation' or str(payment.pk) != str(payment_id):
            raise BusinessError('模拟付款记录已变化，请刷新当前订单。', 'simulation_intent_changed')
        if payment.simulation_state in ('SUCCESS', 'CLOSED'):
            return sync_payment(order_id, user) if payment.status not in ('paid', 'closed') else order
        if payment.expires_at <= timezone.now():
            payment.simulation_state = 'CLOSED'
        else:
            payment.simulation_state = {'success': 'SUCCESS', 'failure': 'CLOSED', 'pending': 'UNKNOWN'}[outcome]
        payment.simulation_settled_at = timezone.now() if payment.simulation_state == 'SUCCESS' else None
        payment.save(update_fields=['simulation_state', 'simulation_settled_at'])
        audit(user, 'simulation_payment_outcome', order.pk, payment_id=str(payment.pk), outcome=outcome)
    return sync_payment(order_id, user)


def simulate_refund(order_id, user, refund_id, outcome):
    from .payments import _order, process_refund
    from .services import audit
    with transaction.atomic():
        order = _order(order_id, user, merchant=True)
        require(order.stall)
        refund = PaymentRefund.objects.filter(order=order, pk=refund_id, mode='simulation').first()
        if order.mode != 'simulation' or not refund:
            raise BusinessError('模拟退款记录不存在，请刷新订单。', 'simulation_intent_changed')
        if refund.status == 'success': return order
        if refund.simulation_state == 'SUCCESS':
            return process_refund(order_id, refund.pk)
        refund.simulation_state = {'success': 'SUCCESS', 'failure': 'ABNORMAL', 'pending': 'PROCESSING'}[outcome]
        refund.simulation_settled_at = timezone.now() if outcome == 'success' else None
        refund.save(update_fields=['simulation_state', 'simulation_settled_at'])
        audit(user, 'simulation_refund_outcome', order.pk, refund_id=str(refund.pk), outcome=outcome)
    return process_refund(order_id, refund.pk)
