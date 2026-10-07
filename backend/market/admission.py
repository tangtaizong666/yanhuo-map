"""Service admission, separate from fulfilment of already accepted orders.

Public information does not imply transaction permission. Only the explicit
non-production demo may rehearse orders without real storefront credentials.
"""
from django.conf import settings
from django.db.models import Q
from django.utils import timezone


# Explicitly share Python's Unicode whitespace set with database regexes.
# PostgreSQL's locale-dependent \s/\S does not reliably match Python str.strip.
ADMISSION_WHITESPACE = (
    '\t\n\v\f\r\x1c\x1d\x1e\x1f \x85\xa0\u1680'
    '\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a'
    '\u2028\u2029\u202f\u205f\u3000'
)
ADMISSION_TEXT_PATTERN = f'[^{ADMISSION_WHITESPACE}]'


def has_admission_text(value):
    return bool(value and value.strip(ADMISSION_WHITESPACE))


def simulated_orders(stall):
    # Offline demo pickup predates the optional online simulator and stays usable.
    return bool(stall.is_demo and settings.DEMO_MODE and not settings.PRODUCTION)


def public_listing_reason(stall):
    if not stall.is_visible: return '摊位尚未公开。'
    if stall.is_demo and not settings.DEMO_MODE: return '示例摊位不在当前环境公开。'
    return ''


def public_listing_q(prefix=''):
    condition = Q(**{prefix + 'is_visible': True})
    if not settings.DEMO_MODE: condition &= Q(**{prefix + 'is_demo': False})
    return condition


def new_trade_eligible(stall):
    return not pickup_eligibility_reason(stall)


def new_trade_eligibility_q(prefix=''):
    """SQL counterpart for bounded discovery filtering before pagination."""
    def q(**values): return Q(**{prefix + key: value for key, value in values.items()})
    verified = q(transaction_enabled=True, merchant__is_verified=True)
    live = q(is_demo=False, merchant__qualification_tier='storefront',
        merchant__license_number__regex=ADMISSION_TEXT_PATTERN,
        merchant__licensed_business_address__regex=ADMISSION_TEXT_PATTERN,
        merchant__food_preparation_address__regex=ADMISSION_TEXT_PATTERN,
        merchant__license_valid_until__gte=timezone.localdate())
    demo = q(is_demo=True) if settings.DEMO_MODE and not settings.PRODUCTION else q(pk__isnull=True)
    return verified & (live | demo)


pickup_eligible_q = new_trade_eligibility_q


def merchant_trade_reason(merchant):
    if merchant.qualification_tier != 'storefront':
        return '此流动摊位仅提供找摊与菜品信息，尚未开放线上下单、支付和配送；可到摊咨询。'
    if not merchant.is_verified: return '商户经营资质尚未通过核验，暂停新的线上交易。'
    if not (has_admission_text(merchant.licensed_business_address) and has_admission_text(merchant.food_preparation_address)):
        return '门店证照地址尚未登记完整，暂停新的线上交易。'
    if not has_admission_text(merchant.license_number):
        return '经营许可证号尚未登记完整，暂停新的线上交易。'
    if merchant.license_valid_until is None:
        return '经营许可证有效期尚未核验，暂停新的线上交易。'
    if merchant.license_valid_until < timezone.localdate():
        return '经营许可证已过有效期，暂停新的线上下单、支付和配送。'
    return ''


def pickup_eligibility_reason(stall):
    """Durable qualification; excludes opening hours, position and busy switches."""
    if stall.is_demo:
        if not simulated_orders(stall): return '示例摊位不能开展真实交易。'
        if not stall.merchant.is_verified: return '示例商户尚未通过模拟交易核验。'
    else:
        reason = merchant_trade_reason(stall.merchant)
        if reason: return reason
    if not stall.transaction_enabled: return '此摊位仅支持线下到访，尚未开放在线点单。'
    return ''


def pickup_unavailable_reason(stall, config=None, *, existing_order=False):
    reason = public_listing_reason(stall) or pickup_eligibility_reason(stall)
    if reason: return reason
    status = stall.effective_status(config)
    if status == 'stale': return '位置确认已过期，请等待商家重新确认。'
    if status == 'closed': return '摊位已收摊，暂不接收新订单。'
    if status == 'paused': return '商家暂时休息，暂不接收新订单。'
    if not existing_order and not stall.accepting_orders:
        return '商家正在忙碌，已暂停接收新订单；仍可查看摊位。'
    if not hasattr(stall, 'location'): return '商家尚未确认取餐位置。'
    if not existing_order:
        gate = stall.new_order_gate_code()
        if gate == 'ordering_stopped': return '本场线上接单已截止，已下订单仍正常处理。'
        if gate == 'prep_capacity_reached': return '正在处理的订单已达上限，请稍后再试；出餐后会自动恢复名额。'
    return ''


def online_payment_eligibility_reason(stall):
    # Existing orders may be paid after closing/busy changes, but cannot initiate
    # a new online charge after transaction qualification is revoked or expires.
    return pickup_eligibility_reason(stall)


def delivery_eligibility_reason(stall):
    from .simulation import eligible
    reason = pickup_eligibility_reason(stall)
    if reason: return reason
    if not eligible(stall) and not stall.delivery_approved: return '配送尚未通过运营准入核验。'
    return ''


def capabilities(stall, context=None):
    from .delivery import delivery_settings
    from .serializers import public_payment_readiness
    context = context if context is not None else {}
    public_reason = public_listing_reason(stall)
    pickup_reason = pickup_eligibility_reason(stall)
    payment_reason = online_payment_eligibility_reason(stall)
    delivery_reason = delivery_eligibility_reason(stall)
    payment = public_payment_readiness(stall, context)
    delivery = delivery_settings(stall, context=context)
    current_pickup_reason = pickup_unavailable_reason(stall, context.get('config'))
    return {
        'mode': 'simulation' if simulated_orders(stall) else 'live',
        'public_listing': {'available': not public_reason, 'reason': public_reason},
        'pickup_orders': {'eligible': not pickup_reason, 'available': not current_pickup_reason, 'reason': current_pickup_reason},
        'online_payment': {'eligible': not payment_reason, 'available': not payment_reason and payment['available'],
            'reason': payment_reason or ('' if payment['available'] else payment['reason'])},
        'delivery_orders': {'eligible': not delivery_reason, 'available': not delivery_reason and delivery['available'],
            'reason': delivery_reason or delivery['reason']},
    }
