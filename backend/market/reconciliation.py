"""Restricted, provider-verified resolution. There is no manual 'mark paid' path."""
import hashlib
import json
import re
import secrets
from datetime import timedelta

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .errors import BusinessError
from .financial import evidence, invalidate_financial_cache, payment_facts, refund_facts
from .models import PaymentAttempt, PaymentRefund
from .payments import (_apply_payment, _apply_refund, _compatible_client, _error, _order,
                       _refresh_money_status, _validate_refund, process_refund)
from .services import audit, release_inventory
from .wechatpay import GatewayError


def require_operator(actor, permission='market.resolve_payments'):
    if not actor or not actor.is_active or not (actor.is_superuser or actor.is_staff and actor.has_perm(permission)):
        raise BusinessError('没有资金核验操作权限。', 'forbidden', status=403)


def _reason(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 200:
        raise BusinessError('请填写不超过200字的核验原因。', 'invalid_reason', status=400)
    return value.strip()


def _live(order, payment=None):
    if order.mode != 'live' or payment and (payment.mode != 'live' or payment.order_id != order.pk):
        raise BusinessError('运营资金核验只处理原有正式支付记录。', 'live_payment_required')


def _observe(order_id, payment_id, actor, reason, *, refund_id=None, external_refund_no=None):
    """Claim a short lease, query outside SQL, append only validated safe facts."""
    require_operator(actor)
    reason = _reason(reason)
    with transaction.atomic():
        order = _order(order_id)
        try: payment = order.payments.get(pk=payment_id)
        except PaymentAttempt.DoesNotExist: raise BusinessError('付款记录不存在。', 'not_found', status=404) from None
        _live(order, payment)
        if refund_id:
            try: record = PaymentRefund.objects.select_related('payment').get(pk=refund_id, order=order, payment=payment)
            except PaymentRefund.DoesNotExist: raise BusinessError('退款记录不存在。', 'not_found', status=404) from None
        else:
            record = payment
        if record.request_in_flight_until and record.request_in_flight_until > timezone.now():
            raise BusinessError('该记录正在查询，请稍后核验。', 'financial_operation_busy')
        from .payment_schedule import admit_query, finish_query
        if not admit_query(record):
            raise BusinessError('刚刚查询过该记录，请稍后核验。', 'payment_query_cooldown',
                next_query_at=record.next_query_at.isoformat())
        lease = timezone.now() + timedelta(seconds=90)
        record.request_in_flight_until = lease
        record.save(update_fields=['request_in_flight_until'])
    data, failure = None, None
    try:
        client = _compatible_client(payment)
        data = client.query_refund(record.out_refund_no) if refund_id else client.query_payment(payment.out_trade_no)
    except GatewayError as exc:
        failure = exc
    with transaction.atomic():
        order = _order(order_id)
        payment = order.payments.get(pk=payment_id)
        record = PaymentRefund.objects.select_related('payment').get(pk=refund_id) if refund_id else payment
        if record.request_in_flight_until != lease:
            raise BusinessError('另一查询已接管此记录，请重新核验。', 'financial_operation_busy')
        if failure is None:
            try:
                if refund_id: _apply_refund(order, record, data)
                else: _apply_payment(order, payment, data)
                if external_refund_no and (payment.status != 'paid' or not payment.transaction_id):
                    raise GatewayError('PAYMENT_CAPTURE_UNVERIFIED', '原付款尚未核验为已收款。')
            except GatewayError as exc:
                failure = exc
        if failure:
            _error(record, failure)
            evidence(order, 'refund_checked' if refund_id else 'payment_checked', 'unverified', reason,
                actor=actor, payment=payment, refund=record if refund_id else None,
                facts={'error_code': failure.code})
        else:
            evidence(order, 'refund_checked' if refund_id else 'payment_checked', 'verified', reason,
                actor=actor, payment=payment, refund=record if refund_id else None,
                facts=refund_facts(payment, data) if refund_id else payment_facts(payment, data))
        continuing = external_refund_no is not None and failure is None
        if not continuing:
            record.request_in_flight_until = None
            record.save(update_fields=['request_in_flight_until'])
            finish_query(record, failed=failure is not None)
    # Raise after commit so an unavailable/invalid gateway cannot erase its evidence.
    if failure:
        raise BusinessError(failure.safe_message, 'financial_verification_failed', status=409)
    if continuing:
        # An unknown external number has no refund record yet. Keep the payment's
        # lease and failure budget through the second HTTP request, so changing
        # numbers cannot bypass pacing and capture success cannot reset failures.
        return _observe_unknown_refund(order_id, payment_id, external_refund_no, actor, reason, lease)
    return data


def _observe_unknown_refund(order_id, payment_id, out_refund_no, actor, reason, lease):
    """Continue the admitted payment observation; never persist an unverified refund."""
    from .payment_schedule import finish_query
    with transaction.atomic():
        order = _order(order_id)
        payment = order.payments.get(pk=payment_id)
        if payment.request_in_flight_until != lease:
            raise BusinessError('另一查询已接管此记录，请重新核验。', 'financial_operation_busy')
    failure = None
    try:
        data = _compatible_client(payment).query_refund(out_refund_no)
    except GatewayError as exc:
        data, failure = None, exc
    with transaction.atomic():
        order = _order(order_id)
        payment = order.payments.get(pk=payment_id)
        if payment.request_in_flight_until != lease:
            raise BusinessError('另一查询已接管此记录，请重新核验。', 'financial_operation_busy')
        existing = PaymentRefund.objects.filter(mchid=payment.mchid, out_refund_no=out_refund_no).first()
        refund = PaymentRefund(order=order, payment=payment, mode=payment.mode,
            out_refund_no=out_refund_no, amount_cents=payment.amount_cents, reason=reason[:80],
            requested_by=actor, source='external')
        if existing:
            # A new record appeared during HTTP. Its own lease/budget must win;
            # retry through the known-record path instead of applying stale work.
            failure = GatewayError('EXTERNAL_REFUND_RECORD_CHANGED', '退款记录已变化，请重新核验。')
        if failure is None:
            try:
                state, _ = _validate_refund(order, refund, data)
                if state != 'SUCCESS':
                    raise GatewayError('EXTERNAL_REFUND_NOT_SUCCESS', '外部退款尚未核验为全额成功，不能结案。')
                _apply_refund(order, refund, data)
                finish_query(refund, failed=False)
            except GatewayError as exc:
                failure = exc
        evidence(order, 'external_refund_checked', 'unverified' if failure else 'verified', reason,
            actor=actor, payment=payment, refund=refund if not refund._state.adding else None,
            facts={'error_code': failure.code} if failure else refund_facts(payment, data))
        payment.request_in_flight_until = None
        payment.save(update_fields=['request_in_flight_until'])
        finish_query(payment, failed=failure is not None)
    if failure:
        raise BusinessError(failure.safe_message, 'financial_verification_failed')
    return data


def verify_external_refund(order_id, payment_id, out_refund_no, actor, reason):
    """Import a full external refund only after a signed query matches its capture."""
    require_operator(actor)
    reason = _reason(reason)
    if not isinstance(out_refund_no, str) or not re.fullmatch(r'[A-Za-z0-9_\-|*@]{1,64}', out_refund_no):
        raise BusinessError('请填写有效的商户退款单号。', 'invalid_refund_number', status=400)
    with transaction.atomic():
        order = _order(order_id)
        try: payment = order.payments.get(pk=payment_id)
        except PaymentAttempt.DoesNotExist: raise BusinessError('付款记录不存在。', 'not_found', status=404) from None
        _live(order, payment)
        existing = PaymentRefund.objects.filter(mchid=payment.mchid, out_refund_no=out_refund_no).first()
        if existing and (existing.order_id != order.pk or existing.payment_id != payment.pk):
            raise BusinessError('退款单号与本付款不匹配。', 'refund_identity_mismatch')
    if existing:
        data = _observe(order_id, payment_id, actor, reason, refund_id=existing.pk)
        if data.get('status') != 'SUCCESS':
            raise BusinessError('外部退款尚未核验为全额成功，不能结案。', 'financial_verification_failed')
    else:
        _observe(order_id, payment_id, actor, reason, external_refund_no=out_refund_no)
    with transaction.atomic():
        return _order(order_id)


def _operation_digest(order_id, payment_id, source, replaces_id, amount, reason):
    return hashlib.sha256(json.dumps([str(order_id), str(payment_id), source,
        str(replaces_id or ''), amount, reason], ensure_ascii=False).encode()).hexdigest()


def request_operator_refund(order_id, payment_id, actor, reason, operation_key, expected_amount_cents, *, replaces_id=None):
    """Explicit CLOSED retry or anomalous-capture compensation, always full amount."""
    require_operator(actor)
    reason = _reason(reason)
    if not isinstance(operation_key, str) or not 8 <= len(operation_key) <= 128:
        raise BusinessError('请使用有效的操作提交标识。', 'invalid_operation_key', status=400)
    source = 'retry' if replaces_id else 'compensation'
    digest = _operation_digest(order_id, payment_id, source, replaces_id, expected_amount_cents, reason)
    with transaction.atomic():
        order = _order(order_id)
        replay = PaymentRefund.objects.filter(operation_key=operation_key).first()
        if replay:
            if replay.operation_hash != digest or replay.order_id != order.pk:
                raise BusinessError('提交标识已用于另一项资金操作。', 'idempotency_conflict')
            replay_id = replay.pk
        else:
            replay_id = None
    if replay_id:
        return process_refund(order_id, replay_id)
    payment_data = _observe(order_id, payment_id, actor, reason)
    if payment_data.get('trade_state') != 'SUCCESS':
        raise BusinessError('原付款目前不是可全额退款的成功状态；请核对外部退款。', 'payment_requires_review')
    if replaces_id:
        data = _observe(order_id, payment_id, actor, reason, refund_id=replaces_id)
        if data.get('status') != 'CLOSED':
            raise BusinessError('只有刚核验已关闭的退款可以创建新尝试。', 'refund_retry_unavailable')
    with transaction.atomic():
        order = _order(order_id)
        payment = order.payments.get(pk=payment_id)
        _live(order, payment)
        replay = PaymentRefund.objects.filter(operation_key=operation_key).first()
        if replay:
            if replay.operation_hash != digest or replay.order_id != order.pk:
                raise BusinessError('提交标识已用于另一项资金操作。', 'idempotency_conflict')
            refund = replay
        else:
            if type(expected_amount_cents) is not int or expected_amount_cents != payment.amount_cents:
                raise BusinessError('确认金额与原付款不符。', 'refund_amount_changed')
            if payment.status != 'paid' or not payment.transaction_id:
                raise BusinessError('原付款尚未核验为已收款。', 'payment_requires_review')
            if payment.request_in_flight_until and payment.request_in_flight_until > timezone.now():
                raise BusinessError('付款正在查询，请稍后处理。', 'financial_operation_busy')
            if payment.refunds.filter(status__in=PaymentRefund.ACTIVE_STATUSES).exists():
                raise BusinessError('已有退款正在处理，不能再发起一笔。', 'refund_in_progress')
            if payment.refunds.filter(status='success').exists():
                raise BusinessError('该付款已有成功退款，不能重复退款。', 'refund_already_succeeded')
            previous = None
            if replaces_id:
                previous = payment.refunds.filter(pk=replaces_id, status='closed', resolved_at__isnull=True).first()
                if previous is None:
                    raise BusinessError('原退款状态已变化，请重新核验。', 'refund_retry_unavailable')
            elif not order.payment_review_required:
                raise BusinessError('正常付款请使用订单退款流程。', 'compensation_unavailable')
            refund = PaymentRefund.objects.create(order=order, payment=payment, requested_by=actor,
                mode=payment.mode, out_refund_no='YHR' + secrets.token_hex(20).upper(),
                amount_cents=payment.amount_cents, reason=reason.encode('utf-8')[:80].decode('utf-8', errors='ignore'),
                source=source, replaces=previous, operation_key=operation_key, operation_hash=digest)
            order.payment_status = 'refunding'
            order.save(update_fields=['payment_status'])
            evidence(order, 'refund_retry_authorized' if previous else 'compensation_authorized', 'queued', reason,
                actor=actor, payment=payment, refund=refund, facts={'amount_cents': payment.amount_cents,
                    'out_refund_no': refund.out_refund_no, 'replaces_id': str(previous.pk) if previous else None})
            audit(actor, 'operator_refund_requested', order.pk, payment_id=str(payment.pk), refund_id=str(refund.pk), source=source)
    return process_refund(order_id, refund.pk)


def verify_and_resolve(order_id, actor, reason):
    """Re-query all known money before resolving; incomplete facts retain the hold."""
    require_operator(actor)
    reason = _reason(reason)
    with transaction.atomic():
        order = _order(order_id)
        _live(order)
        payment_ids = list(order.payments.values_list('pk', flat=True))
    if not payment_ids:
        raise BusinessError('没有可核验的微信付款记录。', 'payment_requires_review')
    observed = {}
    for payment_id in payment_ids:
        observed[payment_id] = _observe(order_id, payment_id, actor, reason)
    with transaction.atomic():
        order = _order(order_id)
        refund_ids = list(order.refunds.values_list('pk', 'payment_id'))
    for refund_id, payment_id in refund_ids:
        _observe(order_id, payment_id, actor, reason, refund_id=refund_id)
    unresolved = None
    with transaction.atomic():
        order = _order(order_id)
        payments = list(order.payments.all())
        refunds = list(order.refunds.all())
        if {p.pk for p in payments} != set(payment_ids) or {r.pk for r in refunds} != {r[0] for r in refund_ids}:
            unresolved = '核验期间交易记录发生变化，请重新核验。'
        elif any(p.status in PaymentAttempt.ACTIVE_STATUSES or p.request_in_flight_until and p.request_in_flight_until > timezone.now() for p in payments):
            unresolved = '仍有付款结果不确定，不能结案。'
        elif any(r.status != 'success' and r.resolved_at is None for r in refunds):
            unresolved = '仍有退款未结案；关闭的退款须先完成补偿。'
        elif any((p.status == 'paid' and observed[p.pk].get('trade_state') not in ('SUCCESS', 'REFUND'))
                 or (p.status == 'closed' and observed[p.pk].get('trade_state') != 'CLOSED') for p in payments):
            unresolved = '核验后收到新的资金状态，请重新查询。'
        refunded = {}
        for refund in refunds:
            if refund.status == 'success': refunded[refund.payment_id] = refunded.get(refund.payment_id, 0) + refund.amount_cents
        captures = [p for p in payments if p.status == 'paid']
        remaining = [p for p in captures if refunded.get(p.pk, 0) < p.amount_cents]
        net = sum(p.amount_cents - refunded.get(p.pk, 0) for p in captures)
        offline = order.payment_method == 'offline' and order.paid_at is not None
        if any(refunded.get(p.pk, 0) not in (0, p.amount_cents) for p in captures):
            unresolved = '存在部分或超额退款，首轮全额退款流程不能结案。'
        elif not unresolved and net:
            if offline or len(remaining) != 1 or net != order.total_cents or order.status in ('cancelled', 'rejected'):
                unresolved = '还有异常或终止订单的净收款，须按原付款逐笔补偿。'
        if unresolved:
            order.payment_review_required = True
            order.save(update_fields=['payment_review_required'])
            evidence(order, 'financial_resolution', 'unresolved', reason, actor=actor,
                facts={'reason': unresolved, 'captured_cents': sum(p.amount_cents for p in captures), 'net_online_cents': net})
        else:
            if not captures and not offline:
                order.payment_status, order.payment_method, order.paid_at = 'unpaid', 'offline', None
            elif offline:
                order.payment_status = 'paid'
            elif net:
                order.payment_status, order.payment_method, order.paid_at = 'paid', 'wechat', remaining[0].paid_at
            else:
                order.payment_status, order.payment_method = 'refunded', 'wechat'
                if order.status not in ('completed', 'cancelled', 'rejected'):
                    if order.status in ('pending', 'pending_payment'): release_inventory(order)
                    order.status, order.cancel_requested = 'cancelled', False
                    order.cancel_reason = '运营已核验全部款项原路退回'
            order.payment_review_required = False
            order.save()
            evidence(order, 'financial_resolution', 'resolved', reason, actor=actor,
                facts={'payment_ids': [str(p.pk) for p in payments], 'refund_ids': [str(r.pk) for r in refunds],
                    'captured_cents': sum(p.amount_cents for p in captures), 'refunded_cents': sum(refunded.values()),
                    'net_online_cents': net, 'payment_status': order.payment_status})
            audit(actor, 'financial_resolution_verified', order.pk)
        invalidate_financial_cache(order)
    if unresolved:
        raise BusinessError(unresolved, 'financial_resolution_incomplete')
    return order
