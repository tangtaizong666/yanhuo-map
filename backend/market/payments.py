"""Persist payment uncertainty; only verified provider facts may move money state.

Every state transition takes the same Order row lock as fulfilment. Stable intents
and bounded operation leases are committed before HTTP; no network request holds
a database transaction open. Unknown outcomes retain the financial hold.
"""
import ipaddress
import secrets
from datetime import timedelta

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .errors import BusinessError
from .models import DeliveryPoint, Order, PaymentAttempt, PaymentRefund, SiteConfiguration, Stall
from .services import audit, payment_busy
from .wechatpay import GatewayError


def client_for(account_key):
    from .payment_config import get_payment_client
    return get_payment_client(account_key)


def readiness(merchant):
    from .payment_config import get_merchant_payment_readiness
    return get_merchant_payment_readiness(merchant)


def _order(order_id, user=None, merchant=False, permission=None):
    query = Order.objects.select_for_update(of=('self',)).select_related('stall__merchant')
    if user is not None:
        if merchant:
            from .permissions import owned_or_permitted
            query = owned_or_permitted(query, user, permission or 'market.add_paymentrefund')
        else: query = query.filter(user=user)
    try: return query.get(pk=order_id)
    except Order.DoesNotExist: raise BusinessError('订单不存在。', 'not_found', status=404)


def _error(record, error, *, unknown=True):
    # Gateway messages are intentionally safe; never persist raw signed responses.
    record.error_code = error.code[:80]
    record.error_message = error.safe_message[:200]
    record.last_checked_at = timezone.now()
    if unknown and record.status not in ('paid', 'closed', 'success', 'abnormal'):
        record.status = 'reconcile'
    record.save()


def _invalid(message='支付结果与订单不符，请联系商家核对。'):
    return GatewayError('PAYMENT_RESULT_MISMATCH', message, retryable=False)


def _timestamp(value):
    if not isinstance(value, str): raise _invalid()
    try: result = parse_datetime(value)
    except ValueError: raise _invalid() from None
    if result is None or timezone.is_naive(result): raise _invalid()
    return result


def _payment_identity(payment, data):
    if not isinstance(data, dict): raise _invalid()
    amount = data.get('amount')
    if (data.get('out_trade_no') != payment.out_trade_no or data.get('mchid') != payment.mchid
            or data.get('appid') != payment.appid):
        raise _invalid()
    # WeChat omits optional amount fields for some unpaid/closed query results.
    if data.get('trade_state') == 'SUCCESS':
        if (not isinstance(amount, dict) or type(amount.get('total')) is not int
                or amount['total'] != payment.amount_cents or amount.get('currency') != payment.currency):
            raise _invalid()
    elif amount is not None:
        if (not isinstance(amount, dict)
                or ('total' in amount and (type(amount['total']) is not int or amount['total'] != payment.amount_cents))
                or ('currency' in amount and amount['currency'] != payment.currency)):
            raise _invalid()


def _compatible_client(payment):
    if payment.mode != payment.order.mode:
        raise _invalid('支付记录与订单模式不一致，请联系运营核对。')
    if payment.mode == 'simulation':
        from .simulation import SimulationClient
        return SimulationClient(payment)
    if payment.mode != 'live':
        raise _invalid('无法识别支付模式，请联系运营核对。')
    client = client_for(payment.account_key)
    if client.mchid != payment.mchid or client.appid != payment.appid:
        raise _invalid('收款配置与原订单不一致，请联系运营核对。')
    return client


def _apply_payment(order, payment, data):
    _payment_identity(payment, data)
    state = data.get('trade_state')
    payment.last_checked_at = timezone.now()
    if state == 'SUCCESS':
        provider_id = data.get('transaction_id')
        if not isinstance(provider_id, str) or not 1 <= len(provider_id) <= 64: raise _invalid()
        paid_at = _timestamp(data.get('success_time'))
        if (payment.transaction_id and payment.transaction_id != provider_id
                or PaymentAttempt.objects.filter(transaction_id=provider_id).exclude(pk=payment.pk).exists()):
            raise _invalid()
        if payment.status == 'paid':
            payment.error_code = payment.error_message = ''
            payment.save(update_fields=['last_checked_at', 'error_code', 'error_message'])
            return
        expected_status = 'pending_payment' if order.fulfillment_type == 'delivery' else 'ready'
        contradictory = (payment.status == 'closed' or order.status != expected_status
            or order.payment_status != 'unpaid' or order.cancel_requested
            or order.payments.exclude(pk=payment.pk).filter(status__in=PaymentAttempt.ACTIVE_STATUSES + ('paid',)).exists())
        payment.status, payment.transaction_id, payment.paid_at = 'paid', provider_id, paid_at
        payment.code_url = payment.h5_url = ''
        payment.error_code = payment.error_message = ''
        payment.save()
        if contradictory:
            # Preserve the captured funds without reviving fulfilment or hiding a double collection.
            order.payment_review_required = True
            order.save(update_fields=['payment_review_required'])
            audit(None, 'payment_requires_review', order.pk, mode=order.mode, payment_id=str(payment.pk))
            return
        order.payment_method, order.payment_status, order.paid_at = 'wechat', 'paid', paid_at
        if order.fulfillment_type == 'delivery':
            # Re-read under the same stall lock as settings/location writes. The
            # gateway observation may have taken seconds since the original read.
            order.stall = Stall.objects.select_for_update(no_key=True).get(pk=order.stall_id)
            from .delivery import active_points, service_enabled, service_approved
            point_open = active_points(order.stall).select_for_update().filter(pk=order.delivery_point_id).exists()
            # Payment success starts the acceptance clock; it never proves delivery.
            # Expired/stopped service is compensated without resurrecting fulfilment.
            # The busy switch stops new reservations, not payment for an already
            # reserved order. All existing operational/qualification checks remain.
            if (paid_at > payment.expires_at or not order.stall.can_order(existing_order=True)
                    or not service_enabled(order.stall) or not service_approved(order.stall)
                    or not order.stall.delivery_starts_at <= timezone.localtime().time().replace(tzinfo=None) < order.stall.delivery_ends_at
                    or not point_open or not order.stall.delivery_points.filter(pk=order.delivery_point_id).exists()):
                from .services import release_inventory
                order.status, order.cancel_reason = 'cancelled', '付款时配送服务已暂停，正在全额退款'
                queue_refund(order, None, '配送服务不可用，全额退款')
                release_inventory(order)
            else:
                order.status = 'pending'
                order.expires_at = timezone.now() + timedelta(minutes=SiteConfiguration.current().pending_minutes)
        order.save()
        audit(None, 'wechat_payment_confirmed', order.pk, mode=order.mode, payment_id=str(payment.pk))
    elif state == 'CLOSED':
        if payment.status == 'paid': raise _invalid()
        payment.status, payment.error_code, payment.error_message = 'closed', '', ''
        payment.code_url = payment.h5_url = ''
        payment.save()
        if (order.payment_status == 'unpaid' and not order.payment_review_required
                and not order.payments.exclude(pk=payment.pk).filter(status__in=PaymentAttempt.ACTIVE_STATUSES + ('paid',)).exists()):
            order.payment_method = 'wechat' if order.fulfillment_type == 'delivery' else 'offline'
            order.save(update_fields=['payment_method'])
    elif state in ('NOTPAY', 'USERPAYING', 'ACCEPT'):
        if payment.status in ('paid', 'closed'): raise _invalid()
        payment.status, payment.error_code, payment.error_message = 'pending', '', ''
        payment.save()
    elif state == 'REFUND':
        # Refund totals are established by the separate refund API, not this coarse trade state.
        if payment.status != 'paid':
            # A lost original callback can be recovered from the signed original
            # capture facts included in a REFUND response. The refund itself still
            # needs a separate full-amount query; never treat REFUND as refunded.
            facts = {**data, 'trade_state': 'SUCCESS'}
            _payment_identity(payment, facts)
            provider_id = data.get('transaction_id')
            if (not isinstance(provider_id, str) or not 1 <= len(provider_id) <= 64
                    or payment.transaction_id and payment.transaction_id != provider_id
                    or PaymentAttempt.objects.filter(transaction_id=provider_id).exclude(pk=payment.pk).exists()):
                raise _invalid()
            payment.status, payment.transaction_id, payment.paid_at = 'paid', provider_id, _timestamp(data.get('success_time'))
            payment.code_url = payment.h5_url = ''
            payment.save()
        if not PaymentRefund.objects.filter(payment=payment, status__in=PaymentRefund.ACTIVE_STATUSES + ('success',)).exists():
            order.payment_review_required = True
            order.save(update_fields=['payment_review_required'])
            payment.error_code = 'EXTERNAL_REFUND_REQUIRES_REVIEW'
            payment.error_message = '检测到商户平台退款，请联系运营核对退款记录。'
            payment.save()
        else:
            payment.save(update_fields=['last_checked_at'])
    else:
        raise _invalid('微信支付状态暂未确认，请稍后重新查询。')


def _drive_payment(order_id, user=None, *, operation='query', payer_client_ip=None, payment_id=None):
    """Lease a stable intent, perform bounded HTTP outside SQL, then apply facts."""
    with transaction.atomic():
        order = _order(order_id, user)
        if operation == 'close' and order.payment_review_required:
            raise BusinessError('此订单存在支付异常，请联系商家与运营核对。', 'payment_requires_review')
        payment = order.payments.filter(pk=payment_id).first() if payment_id else order.payments.exclude(status='closed').first()
        if not payment: return order
        # A terminal payment cannot use the close endpoint to bypass query pacing.
        if operation == 'close' and (payment.status not in PaymentAttempt.ACTIVE_STATUSES or order.payment_status != 'unpaid'):
            return order
        if operation == 'create' and (payment.status != 'creating' or payment.last_checked_at is not None): return order
        if payment.request_in_flight_until and payment.request_in_flight_until > timezone.now(): return order
        from .payment_schedule import admit_query, finish_query
        if operation not in ('create', 'close') and not admit_query(payment): return order
        lease = timezone.now()+timedelta(seconds=90)
        payment.request_in_flight_until = lease
        payment.save(update_fields=['request_in_flight_until'])
    result, failure, entry = None, None, False
    def create(client):
        return client.create_payment(out_trade_no=payment.out_trade_no, amount_cents=payment.amount_cents,
            description=f'{order.stall_name} {"配送" if order.fulfillment_type == "delivery" else "自取"}订单'.encode('utf-8')[:127].decode('utf-8', errors='ignore'),
            expires_at=payment.expires_at, channel=payment.channel, payer_client_ip=payer_client_ip)
    try:
        client = _compatible_client(payment)
        if operation == 'create':
            result, entry = create(client), True
        else:
            try:
                result = client.query_payment(payment.out_trade_no)
            except GatewayError as error:
                if operation == 'recover' and error.code == 'ORDER_NOT_EXIST' and payment.expires_at > timezone.now()+timedelta(seconds=75):
                    result, entry = create(client), True
                else: raise
            if not entry:
                _payment_identity(payment, result)
                auto_close = (order.fulfillment_type == 'delivery' and order.status == 'pending_payment'
                    and payment.expires_at <= timezone.now())
                if (operation == 'close' or auto_close) and result.get('trade_state') in ('NOTPAY', 'USERPAYING', 'ACCEPT'):
                    client.close_payment(payment.out_trade_no)
                    # A verified successful close response establishes closure, never local time.
                    result = {**result, 'trade_state': 'CLOSED'}
    except GatewayError as error:
        failure = error
    with transaction.atomic():
        order = _order(order_id, user)
        payment = PaymentAttempt.objects.get(pk=payment.pk)
        owns_lease = payment.request_in_flight_until == lease
        if owns_lease:
            payment.request_in_flight_until = None
            payment.save(update_fields=['request_in_flight_until'])
        elif failure or entry or not result or result.get('trade_state') != 'SUCCESS':
            # A later operation owns this intent now. Old local failures or entry
            # responses cannot release/overwrite its unresolved financial hold.
            return order
        # A callback can win during HTTP; stale non-payment responses cannot undo it.
        if payment.status == 'paid' and (failure or entry or result and result.get('trade_state') not in ('SUCCESS', 'REFUND')):
            return order
        if failure:
            if operation == 'create' and not failure.outcome_unknown and payment.status == 'creating':
                # This first request failed local preflight before any HTTP call.
                # The same inference is unsafe for a retry of an uncertain request.
                payment.status = 'closed'
                _error(payment, failure, unknown=False)
                order.payment_method = 'wechat' if order.fulfillment_type == 'delivery' else 'offline'
                order.save(update_fields=['payment_method'])
            else: _error(payment, failure)
        elif entry:
            if payment.status in PaymentAttempt.ACTIVE_STATUSES and order.payment_status == 'unpaid' and not order.payment_review_required:
                payment.code_url = result.get('code_url', '') if payment.channel == 'native' else ''
                payment.h5_url = result.get('h5_url', '') if payment.channel == 'h5' else ''
                payment.status, payment.error_code, payment.error_message = 'pending', '', ''
                payment.last_checked_at = timezone.now()
                payment.save()
        elif result is not None:
            try: _apply_payment(order, payment, result)
            except GatewayError as error:
                failure = error
                _error(payment, error)
        if owns_lease and (operation != 'create' or failure):
            finish_query(payment, failed=failure is not None)
        return order


def start_payment(order_id, user, channel, payer_client_ip):
    if channel == 'h5':
        try: ipaddress.ip_address(payer_client_ip)
        except (ValueError, TypeError):
            raise BusinessError('无法确认支付设备的网络地址，请稍后重试。', 'payment_client_ip_missing', status=400) from None
    created = False
    with transaction.atomic():
        order = _order(order_id, user)
        if (channel == 'simulation') != (order.mode == 'simulation'):
            raise BusinessError('付款入口与订单模式不一致，请刷新后使用对应入口。', 'payment_mode_mismatch', status=400)
        delivery = order.fulfillment_type == 'delivery'
        expected_status = 'pending_payment' if delivery else 'ready'
        if order.status != expected_status or order.payment_status != 'unpaid' or order.cancel_requested or order.payment_review_required:
            raise BusinessError('当前订单状态不能发起微信付款，请刷新订单状态。', 'payment_unavailable')
        existing = order.payments.filter(status__in=PaymentAttempt.ACTIVE_STATUSES).first()
        if not existing:
            if delivery and order.expires_at <= timezone.now()+timedelta(seconds=75):
                raise BusinessError('订单剩余付款时间不足，请取消后重新下单。', 'payment_unavailable')
            from .simulation import eligible, payment_readiness
            simulated = order.mode == 'simulation'
            if simulated and not eligible(order.stall):
                raise BusinessError('模拟服务已关闭，原订单不会转为真实支付。', 'simulation_unavailable')
            if order.stall.is_demo and not simulated:
                raise BusinessError('示例摊位不支持真实微信支付，请使用到摊付款流程。', 'payment_not_configured', status=503)
            available = payment_readiness(order.stall) if simulated else readiness(order.stall.merchant)
            if not available.get('available'):
                raise BusinessError(available.get('reason') or '微信支付暂未开通，请使用到摊付款。', 'payment_not_configured', status=503)
            if channel not in available.get('channels', []):
                raise BusinessError('当前微信支付方式尚未开放。', 'payment_channel_unavailable', status=400)
            client = None
            if not simulated:
                try: client = client_for(available['account_key'])
                except GatewayError as error: raise BusinessError(error.safe_message, 'payment_not_configured', status=503) from None
            if (not simulated and (client.enabled is not True or channel not in client.channels)) or not order.stall.merchant.is_verified:
                raise BusinessError('商户收款方式或资质尚未确认。', 'payment_not_configured', status=503)
            PaymentAttempt.objects.create(order=order, merchant=order.stall.merchant,
                mode=order.mode, account_key=available['account_key'],
                mchid=f'SIM{order.stall.merchant_id}' if simulated else client.mchid,
                appid='SIMULATION' if simulated else client.appid,
                out_trade_no='YH' + secrets.token_hex(15).upper(), channel=channel,
                amount_cents=order.total_cents, expires_at=order.expires_at if delivery else timezone.now()+timedelta(minutes=15))
            order.payment_method = 'wechat'
            order.save(update_fields=['payment_method'])
            audit(user, 'wechat_payment_started', order.pk, mode=order.mode)
            created = True
    return _drive_payment(order_id, user, operation='create' if created else 'recover', payer_client_ip=payer_client_ip)


def close_payment(order_id, user):
    return _drive_payment(order_id, user, operation='close')


def _validate_refund(order, refund, data, *, notification=False):
    payment = refund.payment
    if refund.mode != payment.mode or payment.mode != order.mode or refund.mchid and refund.mchid != payment.mchid:
        raise _invalid('退款、付款与订单模式不一致，请联系运营核对。')
    amount = data.get('amount') if isinstance(data, dict) else None
    # Query responses do not include mchid/appid; they are signed under the snapshotted client.
    if (not isinstance(data, dict) or data.get('out_trade_no') != payment.out_trade_no
            or data.get('out_refund_no') != refund.out_refund_no
            or data.get('transaction_id') != payment.transaction_id
            or ('mchid' in data and data['mchid'] != payment.mchid)
            or (notification and data.get('mchid') != payment.mchid)
            or not isinstance(amount, dict) or type(amount.get('total')) is not int
            or amount['total'] != payment.amount_cents or type(amount.get('refund')) is not int
            or amount['refund'] != refund.amount_cents or payment.currency != 'CNY'
            or (amount.get('currency', 'CNY') if notification else amount.get('currency')) != 'CNY'):
        raise _invalid('退款结果与订单不符，请联系运营核对。')
    provider_id = data.get('refund_id')
    if (not isinstance(provider_id, str) or not 1 <= len(provider_id) <= 64
            or refund.refund_id and refund.refund_id != provider_id
            or PaymentRefund.objects.filter(refund_id=provider_id).exclude(pk=refund.pk).exists()):
        raise _invalid()
    state = data.get('refund_status' if notification else 'status')
    if state not in ('SUCCESS', 'PROCESSING', 'CLOSED', 'ABNORMAL'): raise _invalid()
    if state == 'SUCCESS':
        _timestamp(data.get('success_time'))
        refunded = PaymentRefund.objects.filter(payment=payment, status='success').exclude(pk=refund.pk).aggregate(
            total=Sum('amount_cents'))['total'] or 0
        if refunded + refund.amount_cents > payment.amount_cents:
            raise _invalid('已确认退款金额将超过原付款，必须核对所有退款流水。')
    return state, provider_id


def _refresh_money_status(order):
    """Derive money from every captured payment, never just the newest attempt."""
    payments = list(order.payments.filter(status='paid'))
    refunded = {row['payment_id']: row['total'] for row in PaymentRefund.objects.filter(order=order, status='success').values('payment_id').annotate(total=Sum('amount_cents'))}
    outstanding = sum(max(0, p.amount_cents - refunded.get(p.pk, 0)) for p in payments)
    active = PaymentRefund.objects.filter(order=order, status__in=PaymentRefund.ACTIVE_STATUSES).exists()
    if order.payment_method == 'offline' and order.paid_at:
        # An anomalous extra online charge must not erase the original cash receipt.
        order.payment_status = 'refunding' if active else 'paid'
    elif payments:
        order.payment_status = 'refunding' if active else 'paid' if outstanding else 'refunded'
    return outstanding


def _resolve_closed_refunds(order, payment):
    """Full verified success resolves CLOSED even if that closure arrived later."""
    from .financial import evidence
    successes = list(PaymentRefund.objects.filter(payment=payment, status='success'))
    if sum(refund.amount_cents for refund in successes) != payment.amount_cents:
        return
    for previous in PaymentRefund.objects.filter(payment=payment, status='closed', resolved_at__isnull=True):
        previous.resolved_at = timezone.now()
        previous.save(update_fields=['resolved_at'])
        evidence(order, 'refund_superseded', 'resolved', '同付款已核验全额退款成功', payment=payment,
            refund=previous, facts={'successful_refund_ids': [str(refund.pk) for refund in successes],
                'amount_cents': payment.amount_cents})


def _cancel_fully_refunded(order):
    if order.payment_status == 'refunded' and order.status not in ('completed', 'cancelled', 'rejected'):
        if order.status in ('pending', 'pending_payment'):
            from .services import release_inventory
            release_inventory(order)
        order.status, order.cancel_requested = 'cancelled', False
        order.cancel_reason = '商家已全额原路退款'


def _apply_refund(order, refund, data, *, notification=False):
    from .financial import evidence, invalidate_financial_cache, refund_facts
    previous_observation = (refund.status, refund.refund_id, refund.error_code, refund.resolved_at)
    state, provider_id = _validate_refund(order, refund, data, notification=notification)
    if refund.status == 'success':
        if state != 'SUCCESS': raise _invalid()
        refund.last_checked_at = timezone.now()
        refund.save(update_fields=['last_checked_at'])
        _resolve_closed_refunds(order, refund.payment)
        invalidate_financial_cache(order)
        return
    if (refund.status == 'closed' and state in ('PROCESSING', 'ABNORMAL')
            or refund.status == 'abnormal' and state == 'PROCESSING'):
        refund.last_checked_at = timezone.now()
        refund.save(update_fields=['last_checked_at'])
        return
    refund.refund_id, refund.last_checked_at = provider_id, timezone.now()
    refund.error_code = refund.error_message = ''
    if state == 'SUCCESS':
        refund.status, refund.completed_at = 'success', _timestamp(data.get('success_time'))
        refund.resolved_at = timezone.now()
        refund.save()
        # Keep closed provider records intact. A verified full replacement resolves
        # their local follow-up obligation without rewriting CLOSED as SUCCESS.
        _resolve_closed_refunds(order, refund.payment)
        _refresh_money_status(order)
        _cancel_fully_refunded(order)
        # Prepared food is not restocked; completed pickups remain completed.
        order.save(update_fields=['payment_status', 'status', 'cancel_requested', 'cancel_reason', 'inventory_released'])
        audit(None, 'wechat_refund_succeeded', order.pk, mode=order.mode, refund_id=str(refund.pk))
    elif state == 'PROCESSING':
        refund.status, order.payment_status = 'processing', 'refunding'
        order.save(update_fields=['payment_status'])
    elif state == 'CLOSED':
        refund.status = 'closed'
        refund.save()
        _resolve_closed_refunds(order, refund.payment)
        refund.refresh_from_db()
        _refresh_money_status(order)
        _cancel_fully_refunded(order)
        refund.error_message = '微信已关闭该退款申请，请联系商家核对。'
        order.save(update_fields=['payment_status', 'status', 'cancel_requested', 'cancel_reason', 'inventory_released'])
    else:
        refund.status, order.payment_status = 'abnormal', 'refunding'
        refund.error_message = ('模拟退款处理异常，可继续演练退款成功；不会调用微信。' if refund.mode == 'simulation'
            else '微信退款异常，请商家在微信商户平台处理并重新查询。')
        order.save(update_fields=['payment_status'])
    refund.save()
    if previous_observation != (refund.status, refund.refund_id, refund.error_code, refund.resolved_at):
        evidence(order, 'refund_observed', 'verified', '已核验支付服务方退款结果', payment=refund.payment,
            refund=refund, facts=refund_facts(refund.payment, data))
    invalidate_financial_cache(order)


def queue_refund(order, actor, reason):
    """Called with Order locked: commit compensation even if gateway config is down."""
    successful = {row['payment_id']: row['total'] for row in PaymentRefund.objects.filter(order=order, status='success').values('payment_id').annotate(total=Sum('amount_cents'))}
    outstanding = [p for p in order.payments.filter(status='paid') if successful.get(p.pk, 0) < p.amount_cents]
    if len(outstanding) > 1:
        raise BusinessError('存在多笔未结清付款，须由运营逐笔核验补偿。', 'payment_requires_review')
    payment = outstanding[0] if outstanding else None
    if not payment or not payment.transaction_id:
        raise BusinessError('找不到可退款的微信支付记录，请联系运营核对。', 'refund_unavailable')
    existing = PaymentRefund.objects.filter(payment=payment).first()
    if existing: return existing
    refund = PaymentRefund.objects.create(payment=payment, order=order, requested_by=actor,
        mode=payment.mode,
        out_refund_no='YHR' + secrets.token_hex(20).upper(), amount_cents=payment.amount_cents, reason=reason)
    order.payment_status = 'refunding'
    order.save(update_fields=['payment_status'])
    audit(actor, 'wechat_refund_requested', order.pk, mode=order.mode, refund_id=str(refund.pk), amount_cents=refund.amount_cents,
        initiator='user' if actor else 'system')
    from .financial import invalidate_financial_cache
    invalidate_financial_cache(order)
    return refund


def process_refund(order_id, refund_id=None):
    # Lease only the external operation. No database lock is held across HTTP.
    # A crash keeps a stable refund number; the worker queries it after lease expiry.
    with transaction.atomic():
        order = _order(order_id)
        query = PaymentRefund.objects.select_related('payment').filter(order=order)
        refund = query.filter(pk=refund_id).first() if refund_id else query.filter(resolved_at__isnull=True).first() or query.first()
        if not refund or refund.status == 'success' or refund.resolved_at is not None: return order
        if refund.request_in_flight_until and refund.request_in_flight_until > timezone.now(): return order
        from .payment_schedule import admit_query, finish_query
        if not admit_query(refund): return order
        lease = timezone.now()+timedelta(seconds=60)
        refund.request_in_flight_until = lease
        refund.save(update_fields=['request_in_flight_until'])
    result, failure = None, None
    try:
        if refund.mode != refund.payment.mode or refund.mode != order.mode:
            raise _invalid('退款记录模式不一致，请联系运营核对。')
        client = _compatible_client(refund.payment)
        try:
            result = client.query_refund(refund.out_refund_no)
        except GatewayError as error:
            if error.code not in ('RESOURCE_NOT_EXISTS', 'REFUND_NOT_EXIST'): raise
            # A CLOSED refund cannot be replaced or re-created automatically.
            if refund.status == 'closed': raise
            result = client.refund(out_trade_no=refund.payment.out_trade_no, out_refund_no=refund.out_refund_no,
                amount_cents=refund.amount_cents, reason=refund.reason)
    except GatewayError as error:
        failure = error
    with transaction.atomic():
        order = _order(order_id)
        refund = PaymentRefund.objects.select_related('payment').get(pk=refund.pk)
        owns_lease = refund.request_in_flight_until == lease
        if owns_lease:
            refund.request_in_flight_until = None
            refund.save(update_fields=['request_in_flight_until'])
        elif failure or not result or result.get('status') != 'SUCCESS':
            return order
        if refund.status == 'success': return order
        if failure: _error(refund, failure)
        elif result is not None:
            try: _apply_refund(order, refund, result)
            except GatewayError as error:
                failure = error
                _error(refund, error)
        if owns_lease:
            finish_query(refund, failed=failure is not None)
        return order


def sync_payment(order_id, user=None):
    with transaction.atomic():
        order = _order(order_id, user)
        refunds = list(PaymentRefund.objects.filter(order=order, resolved_at__isnull=True).exclude(status='success').values_list('pk', flat=True))
        payment_query = order.payments.filter(status__in=PaymentAttempt.ACTIVE_STATUSES)
        if order.payment_review_required:
            payment_query = order.payments.exclude(status='closed')
        payments = list(payment_query.values_list('pk', flat=True))
    for refund_id in refunds:
        order = process_refund(order_id, refund_id)
    for payment_id in payments:
        order = _drive_payment(order_id, user, payment_id=payment_id)
    if not refunds and not payments:
        # Paid orders can reveal an external refund; only a verified operator
        # resolution can then clear the financial-review hold.
        order = _drive_payment(order_id, user)
    return order


def request_refund(order_id, user, reason, simulation_outcome=None):
    if not isinstance(reason, str) or not reason.strip() or len(reason.encode('utf-8')) > 80:
        raise BusinessError('请简要填写退款原因，最多约 26 个汉字。', 'invalid_reason', status=400)
    with transaction.atomic():
        order = _order(order_id, user, merchant=True)
        if order.mode == 'simulation':
            from .simulation import require
            require(order.stall)
        if simulation_outcome is not None:
            from .simulation import require
            require(order.stall)
            if order.mode != 'simulation':
                raise BusinessError('此订单不是模拟订单。', 'simulation_unavailable')
        existing = PaymentRefund.objects.filter(order=order, resolved_at__isnull=True).exclude(status='success').select_related('payment').first()
        if not existing and order.payment_status == 'refunded':
            existing = PaymentRefund.objects.filter(order=order, status='success').first()
        if not existing:
            refundable = ('pending', 'preparing', 'ready', 'delivering', 'arrived', 'completed') if order.fulfillment_type == 'delivery' else ('ready', 'completed')
            if (order.status not in refundable or order.payment_method != 'wechat'
                or order.payment_status != 'paid' or payment_busy(order)):
                raise BusinessError('此订单当前不能申请原路退款。', 'refund_unavailable')
            refund = queue_refund(order, user, reason)
            if simulation_outcome is not None:
                refund.simulation_state = {'success': 'SUCCESS', 'pending': 'PROCESSING', 'failure': 'ABNORMAL'}[simulation_outcome]
                refund.save(update_fields=['simulation_state'])
            if order.fulfillment_type == 'delivery' and order.status != 'completed':
                if order.status == 'pending':
                    from .services import release_inventory
                    release_inventory(order)
                order.status, order.cancel_requested, order.cancel_reason = 'cancelled', False, reason
                order.save()
    return process_refund(order_id, existing.pk if existing else refund.pk)


def handle_notification(account_key, headers, body):
    """Verify and durably enqueue; business effects belong to the inbox worker."""
    from .notification_inbox import receive_notification
    return receive_notification(account_key, headers, body)


def apply_notification(account_key, mchid, appid, event_type, resource):
    """Apply previously verified minimal facts inside the caller's transaction."""
    if account_key.startswith('simulation-'):
        raise _invalid('模拟记录不接收真实支付通知。')
    if event_type == 'TRANSACTION.SUCCESS':
        candidate = PaymentAttempt.objects.filter(mode='live', order__mode='live', account_key=account_key, out_trade_no=resource.get('out_trade_no')).first()
        if not candidate: raise _invalid('支付通知找不到对应订单。')
        with transaction.atomic():
            order = _order(candidate.order_id)
            payment = PaymentAttempt.objects.get(pk=candidate.pk)
            if mchid != payment.mchid or appid != payment.appid: raise _invalid()
            if resource.get('trade_state') != 'SUCCESS': raise _invalid()
            _apply_payment(order, payment, resource)
    elif event_type in ('REFUND.SUCCESS', 'REFUND.ABNORMAL', 'REFUND.CLOSED'):
        candidate = PaymentRefund.objects.filter(mode='live', order__mode='live', payment__mode='live', payment__account_key=account_key,
            mchid=mchid, out_refund_no=resource.get('out_refund_no')).select_related('payment').first()
        if not candidate: raise _invalid('退款通知找不到对应订单。')
        with transaction.atomic():
            order = _order(candidate.order_id)
            refund = PaymentRefund.objects.select_related('payment').get(pk=candidate.pk)
            if mchid != refund.payment.mchid or appid != refund.payment.appid: raise _invalid()
            expected = {'REFUND.SUCCESS': 'SUCCESS', 'REFUND.ABNORMAL': 'ABNORMAL', 'REFUND.CLOSED': 'CLOSED'}[event_type]
            if resource.get('refund_status') != expected: raise _invalid()
            _apply_refund(order, refund, resource, notification=True)
    else:
        raise _invalid('暂不支持此类支付通知。')
