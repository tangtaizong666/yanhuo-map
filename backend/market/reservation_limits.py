"""Checkout budgets, checked under the existing account lock after replay lookup."""
import logging
import math
from datetime import timedelta

from django.conf import settings
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from .errors import BusinessError
from .models import Order, PaymentAttempt, PaymentRefund

logger = logging.getLogger('market')
ACTIVE = ('pending_payment', 'pending', 'preparing', 'ready', 'delivering', 'arrived')


def enforce_reservation_limits(user, stall, items):
    quantity = sum(item['quantity'] for item in items)
    if quantity > settings.CHECKOUT_MAX_PORTIONS:
        raise BusinessError(f'每笔订单合计最多 {settings.CHECKOUT_MAX_PORTIONS} 份，请调整餐袋后重试。',
                            'order_quantity_limit', limit=settings.CHECKOUT_MAX_PORTIONS)
    owned = Order.objects.filter(user=user)
    held = owned.annotate(
        uncertain=Exists(PaymentAttempt.objects.filter(order_id=OuterRef('pk'), status__in=PaymentAttempt.ACTIVE_STATUSES)),
        unresolved=Exists(PaymentRefund.objects.filter(order_id=OuterRef('pk'), resolved_at__isnull=True).exclude(status='success')),
    # Match financial.hold_code: inventory release and fulfillment completion do
    # not settle unknown payments/refunds. Ordinary paid terminal orders are free.
    ).filter(Q(status__in=ACTIVE) | Q(payment_review_required=True) |
        Q(payment_status='refunding') | Q(uncertain=True) | Q(unresolved=True))
    same = held.filter(stall=stall)
    if same.count() >= settings.CHECKOUT_MAX_ACTIVE_PER_STALL:
        logger.info('checkout_rejected reason=stall_reservation_limit')
        raise BusinessError('这个摊位还有未完成的订单，请先查看原订单。餐袋已保留。', 'stall_reservation_limit',
            limit=settings.CHECKOUT_MAX_ACTIVE_PER_STALL, order_ids=[str(pk) for pk in same.order_by('created_at').values_list('pk', flat=True)[:10]])
    if held.count() >= settings.CHECKOUT_MAX_ACTIVE_TOTAL:
        logger.info('checkout_rejected reason=active_reservation_limit')
        raise BusinessError(f'最多同时保留 {settings.CHECKOUT_MAX_ACTIVE_TOTAL} 笔未完成订单，请先处理已有订单。',
            'active_reservation_limit', limit=settings.CHECKOUT_MAX_ACTIVE_TOTAL,
            order_ids=[str(pk) for pk in held.order_by('created_at').values_list('pk', flat=True)[:10]])
    now = timezone.now()
    for seconds, limit in ((60, settings.CHECKOUT_PER_MINUTE), (3600, settings.CHECKOUT_PER_HOUR)):
        recent = owned.filter(created_at__gt=now-timedelta(seconds=seconds)).order_by('created_at')
        if recent.count() >= limit:
            oldest = recent.values_list('created_at', flat=True).first()
            retry = max(1, math.ceil((oldest + timedelta(seconds=seconds) - now).total_seconds()))
            logger.info('checkout_rejected reason=checkout_rate_limited')
            raise BusinessError('下单过于频繁，请稍后再试。原订单和餐袋仍保留。', 'checkout_rate_limited',
                                status=429, retry_after=retry)
