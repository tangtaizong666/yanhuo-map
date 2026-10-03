"""Merchant self-delivery to operator-approved campus handoff points only."""
from django.utils import timezone
from .errors import BusinessError
from .models import DeliveryPoint, Order

ACTIVE_DELIVERY_STATUSES = ('pending_payment', 'pending', 'preparing', 'ready', 'delivering', 'arrived')


def point_data(point):
    return {key: getattr(point, key) for key in ('id', 'name', 'address', 'latitude', 'longitude', 'area_id', 'is_simulation')}


def service_enabled(stall):
    from .simulation import eligible
    return stall.simulation_delivery_enabled if eligible(stall) else stall.delivery_enabled


def service_approved(stall):
    from .simulation import eligible
    return eligible(stall) or stall.delivery_approved


def available_points(stall):
    from .simulation import eligible
    query = DeliveryPoint.objects.filter(area_id=stall.area_id)
    return query.filter(is_simulation=True) if eligible(stall) else query.filter(is_active=True, is_simulation=False)


def active_points(stall):
    return available_points(stall).filter(stalls=stall).order_by('id')


def delivery_settings(stall, *, merchant=False, context=None):
    from .serializers import public_payment_readiness
    from .simulation import eligible
    simulated = eligible(stall)
    context = context if context is not None else {}
    if hasattr(stall, '_delivery_points'):
        points = [point for point in stall._delivery_points if point.area_id == stall.area_id
            and (point.is_simulation if simulated else point.is_active and not point.is_simulation)]
    else:
        points = list(active_points(stall))
    result = {name: getattr(stall, 'delivery_' + name) for name in (
        'enabled', 'approved', 'fee_cents', 'min_order_cents', 'eta_min_minutes', 'eta_max_minutes', 'capacity')}
    result.update(starts_at=stall.delivery_starts_at.strftime('%H:%M'), ends_at=stall.delivery_ends_at.strftime('%H:%M'),
        mode='simulation' if simulated else 'live', enabled=service_enabled(stall),
        points=[point_data(p) for p in points], point_ids=[p.pk for p in points])
    reason = ''
    if not service_approved(stall): reason = '配送尚未通过运营准入核验。'
    elif not service_enabled(stall): reason = '商家暂未开启配送。'
    elif stall.is_demo and not simulated: reason = '示例摊位不发起真实配送与微信扣款。'
    elif not public_payment_readiness(stall, context).get('available'): reason = '配送需要先完成微信支付，商家尚未开通线上收款。'
    elif not stall.can_order(context.get('config')): reason = '摊位当前无法接单，请查看营业状态和位置确认时间。'
    elif not result['points']: reason = '暂未设置本校园已获准开放的交接点。'
    elif not (stall.delivery_starts_at < stall.delivery_ends_at and 0 < stall.delivery_eta_min_minutes <= stall.delivery_eta_max_minutes and stall.delivery_capacity > 0):
        reason = '配送参数尚未配置完整，请联系商家。'
    elif not stall.delivery_starts_at <= timezone.localtime().time().replace(tzinfo=None) < stall.delivery_ends_at:
        reason = f'配送接单时间为 {result["starts_at"]}—{result["ends_at"]}。'
    elif (stall.delivery_active_count if hasattr(stall, 'delivery_active_count') else
            Order.objects.filter(stall=stall, fulfillment_type='delivery', status__in=ACTIVE_DELIVERY_STATUSES).count()) >= stall.delivery_capacity:
        reason = '当前配送订单已满，请稍后再试或选择自取。'
    result.update(available=not reason, reason=reason)
    if merchant:
        result['available_points'] = [point_data(p) for p in available_points(stall).order_by('id')]
    return result


def check_delivery(stall, data, subtotal):
    ready = delivery_settings(stall)
    if not ready['available']: raise BusinessError(ready['reason'], 'delivery_unavailable')
    try:
        point = active_points(stall).select_for_update().get(pk=data.get('delivery_point_id'))
    except DeliveryPoint.DoesNotExist:
        raise BusinessError('请选择商家支持且已获准开放的校园交接点。', 'delivery_point_unavailable', status=400) from None
    if subtotal < stall.delivery_min_order_cents:
        raise BusinessError('餐费尚未达到该商家的配送起送金额。', 'delivery_minimum', min_order_cents=stall.delivery_min_order_cents)
    if data.get('expected_delivery_fee_cents') != stall.delivery_fee_cents:
        raise BusinessError('配送费已更新，请确认最新费用后重新提交。', 'delivery_fee_changed', delivery_fee_cents=stall.delivery_fee_cents)
    return point
