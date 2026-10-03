"""One server-owned financial hold and action contract for every order surface."""
import hashlib
import json

from django.utils import timezone

from .errors import BusinessError
from .models import FinancialEvidence, PaymentAttempt, PaymentRefund


def refund_rows(order):
    return list(order.refunds.all())


def current_refund(order):
    rows = refund_rows(order)
    # An old unresolved attempt must not disappear behind a newer success on a
    # different captured payment. Model ordering is newest first.
    return next((r for r in rows if r.status != 'success' and r.resolved_at is None), rows[0] if rows else None)


def hold_code(order):
    if order.payment_review_required:
        return 'payment_requires_review'
    if any(r.status != 'success' and r.resolved_at is None for r in refund_rows(order)):
        return 'refund_unresolved'
    if order.payment_status == 'refunding':
        return 'refund_unresolved'
    if any(p.status in PaymentAttempt.ACTIVE_STATUSES for p in order.payments.all()):
        return 'payment_in_progress'
    return ''


HOLD_MESSAGES = {
    'payment_requires_review': '付款存在异常，须由运营核验资金后才能继续处理。',
    'refund_unresolved': '退款尚未核验结案，暂不能收款、核销或继续履约。',
    'payment_in_progress': '支付结果尚未确认，请先查询或关闭支付。',
}


def hold_reason(order):
    return HOLD_MESSAGES.get(hold_code(order), '')


def require_no_financial_hold(order):
    code = hold_code(order)
    if code:
        raise BusinessError(HOLD_MESSAGES[code], code)


def allowed_actions(order, *, merchant=False):
    """Capabilities describe state, not permission grants; mutation APIs recheck."""
    from .simulation import eligible, require_order_operation
    rows = list(order.payments.all())
    refunds = refund_rows(order)
    held = bool(hold_code(order))
    delivery = order.fulfillment_type == 'delivery'
    pending = order.status in ('pending', 'pending_payment')
    actions = []
    if rows or refunds:
        actions.append('sync_payment')
    try:
        require_order_operation(order)
    except BusinessError:
        return actions
    if merchant:
        latest = current_refund(order)
        if (latest and latest.mode == 'simulation' and latest.status != 'success'
                and eligible(order.stall) and latest.resolved_at is None):
            actions += ['simulate_refund_success', 'simulate_refund_failure', 'simulate_refund_pending']
        if held:
            return actions
        if order.status == 'pending' and order.expires_at > timezone.now() and (not delivery or order.payment_status == 'paid'):
            actions += ['accept', 'reject']
        if order.status == 'preparing' and not order.cancel_requested and (not delivery or order.payment_status == 'paid'):
            actions += ['update_prep', 'ready']
        if order.cancel_requested and order.status in (('preparing', 'ready', 'delivering', 'arrived') if delivery else ('preparing', 'ready')):
            actions.append('deny_cancel')
            if order.payment_status in (('unpaid', 'paid') if delivery else ('unpaid',)):
                actions.append('approve_cancel')
        if not order.cancel_requested:
            if not delivery and order.status == 'ready' and order.payment_status == 'unpaid' and order.payment_method == 'offline':
                actions.append('confirm_payment')
            if order.payment_status == 'paid':
                if delivery and order.status in ('preparing', 'ready', 'delivering', 'arrived'):
                    actions.append('resolve_delivery_issue' if order.delivery_issue else 'report_delivery_issue')
                if not order.delivery_issue:
                    if delivery and order.status == 'ready': actions.append('dispatch')
                    if delivery and order.status == 'delivering': actions.append('arrive')
                    if order.status == ('arrived' if delivery else 'ready'): actions.append('complete')
        refundable = ('pending', 'preparing', 'ready', 'delivering', 'arrived', 'completed') if delivery else ('ready', 'completed')
        if order.status in refundable and order.payment_status == 'paid' and order.payment_method == 'wechat':
            actions.append('refund')
        return actions
    active_payment = next((p for p in rows if p.status in PaymentAttempt.ACTIVE_STATUSES), None)
    if (active_payment and order.payment_status == 'unpaid' and not order.payment_review_required
            and not any(r.status != 'success' and r.resolved_at is None for r in refunds)):
        actions.append('close_payment')
        if active_payment.mode == 'simulation' and eligible(order.stall) and active_payment.simulation_state not in ('SUCCESS', 'CLOSED'):
            actions.append('simulate_payment')
    payable = order.status == ('pending_payment' if delivery else 'ready') and order.payment_status == 'unpaid' and not order.cancel_requested
    if payable and not order.payment_review_required and not refunds:
        actions.append('pay')
    if not held:
        if order.status not in ('completed', 'cancelled', 'rejected') and (order.payment_status == 'unpaid' or delivery and order.payment_status == 'paid'):
            actions.append('cancel')
        if delivery and order.status == 'arrived' and order.payment_status == 'paid' and not order.cancel_requested and not order.delivery_issue:
            actions.append('confirm_receipt')
    if pending and order.expires_at <= timezone.now():
        actions = [action for action in actions if action not in ('pay', 'accept')]
    return actions


def evidence(order, operation, outcome, reason, *, actor=None, payment=None, refund=None, facts=None):
    # Never store raw notifications, payer identity, credentials, or payment URLs.
    facts = facts or {}
    encoded = json.dumps(facts, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return FinancialEvidence.objects.create(order=order, payment=payment, refund=refund, actor=actor,
        operation=operation, outcome=outcome, reason=reason[:200], facts=facts,
        facts_hash=hashlib.sha256(encoded).hexdigest())


def payment_facts(payment, data):
    return {'account_key': payment.account_key, 'mchid': payment.mchid, 'appid': payment.appid,
        **{key: data[key] for key in ('out_trade_no', 'transaction_id', 'trade_state', 'amount', 'success_time') if key in data}}


def refund_facts(payment, data):
    return {'account_key': payment.account_key, 'mchid': payment.mchid,
        **{key: data[key] for key in ('out_trade_no', 'transaction_id', 'out_refund_no', 'refund_id', 'status', 'refund_status', 'amount', 'success_time') if key in data}}


def invalidate_financial_cache(order):
    cache = getattr(order, '_prefetched_objects_cache', {})
    cache.pop('refunds', None)
    cache.pop('payments', None)
