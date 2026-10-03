import hashlib
import json
import secrets
import logging
from datetime import timedelta
from django.conf import settings
from django.db import IntegrityError, connection, transaction
from django.db.models import Exists, F, OuterRef, Q
from django.utils import timezone
from .errors import BusinessError
from .models import AuditLog, Event, Order, OrderItem, PaymentAttempt, PaymentRefund, PreparationRequest, Product, Stall, SiteConfiguration
from .tastes import canonical_item, validate_portions


def payment_busy(order):
    from .financial import hold_code
    return bool(hold_code(order))


def audit(actor, action, target, **details):
    AuditLog.objects.create(actor=actor if actor and actor.is_authenticated else None,
        action=action, target=str(target), details=details)


def release_inventory(order):
    if order.inventory_released: return
    for item in order.items.order_by('product_id'):
        Product.objects.filter(pk=item.product_id).update(stock=F('stock') + item.quantity, stock_version=F('stock_version')+1)
    order.inventory_released = True


def expire_locked(order):
    if order.mode == 'simulation':
        from .simulation import require_order_operation
        try: require_order_operation(order)
        except BusinessError: return False
    if order.status in ('pending', 'pending_payment') and order.expires_at <= timezone.now():
        if payment_busy(order):
            return False
        if order.payment_status != 'unpaid':
            if order.fulfillment_type != 'delivery' or order.payment_status != 'paid': return False
            from .payments import queue_refund
            queue_refund(order, None, '商家接单超时，全额退款')
        order.cancel_reason = '未在规定时间内付款，订单已取消' if order.status == 'pending_payment' else '商家超时未接单，订单已自动取消'
        order.status = 'cancelled'
        release_inventory(order)
        order.save()
        Event.objects.create(type='order_expired', user=order.user, stall=order.stall)
        audit(None, 'order_expired', order.pk)
        return True
    return False


def expire_pending_orders(*, limit=100, stall_id=None, user_id=None, on_error=None):
    """Bounded maintenance; locked/financially held rows never block other orders."""
    candidates = Order.objects.filter(status__in=['pending', 'pending_payment'], expires_at__lte=timezone.now(),
        payment_review_required=False).filter(Q(payment_status='unpaid') | Q(fulfillment_type='delivery', payment_status='paid'))
    candidates = candidates.annotate(_active_payment=Exists(PaymentAttempt.objects.filter(
        order_id=OuterRef('pk'), status__in=PaymentAttempt.ACTIVE_STATUSES)),
        _unresolved_refund=Exists(PaymentRefund.objects.filter(order_id=OuterRef('pk'), resolved_at__isnull=True).exclude(status='success'))
    ).filter(_active_payment=False, _unresolved_refund=False)
    if stall_id is not None: candidates = candidates.filter(stall_id=stall_id)
    if user_id is not None: candidates = candidates.filter(user_id=user_id)
    from .simulation import enabled
    if not enabled():
        basic_cash = Q(fulfillment_type='pickup', payment_method='offline', stall__is_demo=True)
        if not settings.DEMO_MODE or settings.PRODUCTION: basic_cash = Q(pk__isnull=True)
        candidates = candidates.exclude(Q(mode='simulation') & ~basic_cash)
    count, seen = 0, []
    for _ in range(max(1, min(int(limit), 1000))):
        order_id = None
        try:
            with transaction.atomic():
                locks = {'of': ('self',), 'skip_locked': True} if connection.features.has_select_for_update_skip_locked else {}
                # Skip locked rows in the claiming query itself, before LIMIT.
                # A locked oldest batch therefore cannot starve later work.
                order = candidates.exclude(pk__in=seen).select_for_update(**locks).order_by('expires_at', 'pk').first()
                if order is None: break
                order_id = order.pk
                seen.append(order_id)
                count += bool(expire_locked(order))
        except Exception as exc:
            logging.getLogger('market').warning('order_expiry_failed order=%s type=%s', order_id, type(exc).__name__)
            if on_error is not None: on_error(exc)
            if order_id is None: break
    return count


def create_order(user, data):
    payload = {**data, 'items': sorted((canonical_item(item) for item in data['items']), key=lambda x: x['product_id'])}
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    def check_duplicate(existing):
        if existing.request_hash != fingerprint:
            raise BusinessError('本次提交标识已用于其他订单，请刷新后重试。', 'idempotency_conflict')
        return existing, False
    existing = Order.objects.filter(user=user, idempotency_key=data['idempotency_key']).first()
    if existing: return check_duplicate(existing)
    expire_pending_orders(stall_id=data['stall_id'])
    try:
        with transaction.atomic():
            # Lock the account to serialize checkout with account deletion.
            # NO KEY UPDATE serializes account deactivation without blocking audit
            # foreign-key KEY SHARE locks while a concurrent cancellation restores stock.
            if not user.__class__.objects.select_for_update(no_key=True).filter(pk=user.pk, is_active=True).exists():
                raise BusinessError('登录状态已失效，请重新登录。', 'authentication_required', status=403)
            try:
                # Allow concurrent audit/event FK checks while serializing status,
                # location changes and reservations for this stall.
                candidates = Stall.objects.select_for_update(no_key=True).filter(is_visible=True)
                if not settings.DEMO_MODE: candidates = candidates.filter(is_demo=False)
                stall = candidates.get(pk=data['stall_id'])
            except Stall.DoesNotExist:
                raise BusinessError('摊位不存在。', 'not_found', status=404)
            existing = Order.objects.filter(user=user, idempotency_key=data['idempotency_key']).first()
            if existing: return check_duplicate(existing)
            config = SiteConfiguration.current()
            if not stall.can_order(config):
                gate = stall.new_order_gate_code() if stall.accepting_orders and stall.can_order(config, existing_order=True) else ''
                raise BusinessError(stall.order_unavailable_reason(config), gate or 'stall_unavailable')
            if not hasattr(stall, 'location'):
                raise BusinessError('商家尚未确认取餐位置。', 'location_missing')
            ids = [item['product_id'] for item in data['items']]
            products = {p.id: p for p in Product.objects.select_for_update().filter(stall=stall, id__in=ids, is_active=True).order_by('id')}
            if len(products) != len(ids):
                raise BusinessError('订单含有无效商品或不同摊位的商品。', 'invalid_items', status=400)
            changed = [{'product_id': i['product_id'], 'price_cents': products[i['product_id']].price_cents}
                for i in data['items'] if i['expected_price_cents'] != products[i['product_id']].price_cents]
            if changed: raise BusinessError('商品价格已更新，请确认最新价格后重新提交。', 'price_changed', products=changed)
            total = 0
            for item in data['items']:
                product = products[item['product_id']]
                if product.sale_paused:
                    raise BusinessError(f'{product.name}已暂停供应，请重新选择。', 'product_sale_paused', product_id=product.pk)
                validate_portions(product, item)
                if product.stock < item['quantity']:
                    raise BusinessError(f'{product.name}库存不足，剩余{product.stock}份。', 'out_of_stock', product_id=product.id)
                total += product.price_cents * item['quantity']
            if total > 1000000: raise BusinessError('单笔订单金额超出限制。', 'amount_limit', status=400)
            fulfillment = data.get('fulfillment_type', 'pickup')
            delivery = {}
            if fulfillment == 'delivery':
                from .delivery import check_delivery
                point = check_delivery(stall, data, total)
                delivery = dict(delivery_point=point, delivery_point_name=point.name, delivery_point_address=point.address,
                    delivery_point_latitude=point.latitude, delivery_point_longitude=point.longitude,
                    recipient_name=data.get('recipient_name', ''), delivery_fee_cents=stall.delivery_fee_cents,
                    delivery_eta_min_at=timezone.now()+timedelta(minutes=stall.delivery_eta_min_minutes),
                    delivery_eta_max_at=timezone.now()+timedelta(minutes=stall.delivery_eta_max_minutes),
                    status='pending_payment', payment_method='wechat')
                total += stall.delivery_fee_cents
                if total > 1000000: raise BusinessError('单笔订单金额超出限制。', 'amount_limit', status=400)
            loc = stall.location
            order = Order.objects.create(user=user, stall=stall, stall_name=stall.name, stall_image=stall.image,
                mode='simulation' if stall.is_demo else 'live',
                total_cents=total, fulfillment_type=fulfillment, **delivery,
                expires_at=timezone.now()+timedelta(minutes=15 if fulfillment == 'delivery' else config.pending_minutes),
                pickup_address=loc.address, pickup_latitude=loc.latitude, pickup_longitude=loc.longitude,
                note=data['note'], contact_phone=data['contact_phone'], idempotency_key=data['idempotency_key'], request_hash=fingerprint)
            for item in data['items']:
                product = products[item['product_id']]
                # Conditional update also protects against overselling on SQLite demo.
                count = Product.objects.filter(pk=product.id, stock__gte=item['quantity']).update(stock=F('stock')-item['quantity'], stock_version=F('stock_version')+1)
                if not count: raise BusinessError('库存已变化，请重新选择。', 'out_of_stock')
                OrderItem.objects.create(order=order, product=product, name=product.name, image=product.image,
                    unit_price_cents=product.price_cents, quantity=item['quantity'], portions=item.get('portions', []))
            Event.objects.create(type='order_created', user=user, stall=stall)
            audit(user, 'order_created', order.pk)
            return order, True
    except IntegrityError:
        existing = Order.objects.filter(user=user, idempotency_key=data['idempotency_key']).first()
        if existing: return check_duplicate(existing)
        raise


def cancel_order(order_id, user, reason):
    with transaction.atomic():
        try: order = Order.objects.select_for_update().get(pk=order_id, user=user)
        except Order.DoesNotExist: raise BusinessError('订单不存在。', 'not_found', status=404)
        if order.mode == 'simulation':
            from .simulation import require_order_operation
            require_order_operation(order)
        if expire_locked(order): return order
        if order.status in ('cancelled', 'rejected'): return order
        delivery_paid = order.fulfillment_type == 'delivery' and order.payment_status == 'paid'
        if order.status == 'completed' or order.payment_status != 'unpaid' and not delivery_paid:
            raise BusinessError('已收款或已完成的订单不能在线取消，请联系商家。', 'cannot_cancel')
        if payment_busy(order):
            raise BusinessError('微信支付结果尚未确认，请先在订单页查询并关闭支付，再申请取消。', 'payment_in_progress')
        order.cancel_reason = reason or '用户取消'
        if order.status in ('pending', 'pending_payment'):
            if delivery_paid:
                from .payments import queue_refund
                queue_refund(order, user, '用户接单前取消，全额退款')
            order.status = 'cancelled'
            release_inventory(order)
        else:
            order.cancel_requested = True
        order.save()
        audit(user, 'cancel_requested' if order.cancel_requested else 'order_cancelled', order.pk)
        return order


def merchant_action(order_id, user, action, code='', reason='', *, prep_minutes=None, idempotency_key=''):
    with transaction.atomic():
        from .permissions import owned_or_permitted
        query = owned_or_permitted(Order.objects.select_for_update(of=('self',)), user, 'market.change_order')
        try: order = query.get(pk=order_id)
        except Order.DoesNotExist: raise BusinessError('订单不存在。', 'not_found', status=404)
        if order.mode == 'simulation':
            from .simulation import require_order_operation
            require_order_operation(order)
        prep_action = action in ('accept', 'update_prep')
        request_hash = ''
        if prep_action and idempotency_key:
            request_hash = hashlib.sha256(json.dumps({'action': action, 'prep_minutes': prep_minutes,
                'reason': reason if action == 'update_prep' else ''}, sort_keys=True).encode()).hexdigest()
            previous = PreparationRequest.objects.filter(order=order, idempotency_key=idempotency_key).first()
            if previous:
                if previous.request_hash != request_hash:
                    raise BusinessError('本次提交标识已用于其他备餐操作，请刷新后重试。', 'idempotency_conflict')
                return order
        if prep_action and prep_minutes is not None and (isinstance(prep_minutes, bool) or not isinstance(prep_minutes, int) or not 1 <= prep_minutes <= 180):
            raise BusinessError('备餐预估需要填写1至180分钟。', 'invalid_prep_minutes', status=400)
        if action == 'update_prep' and (prep_minutes is None or not idempotency_key or not reason.strip() or len(reason) > 200):
            raise BusinessError('请填写新的备餐预估、调整说明及提交标识。', 'invalid_prep_update', status=400)
        if expire_locked(order): return order
        delivery = order.fulfillment_type == 'delivery'
        if action in ('accept', 'update_prep', 'ready', 'dispatch', 'arrive', 'confirm_payment', 'approve_cancel', 'complete', 'reject', 'deny_cancel'):
            from .financial import require_no_financial_hold
            require_no_financial_hold(order)
        if delivery and order.delivery_issue and action in ('dispatch', 'arrive', 'complete'):
            raise BusinessError('请先处理并记录配送异常的解决结果。', 'delivery_issue_unresolved')
        if action == 'accept' and order.status == 'pending' and (not delivery or order.payment_status == 'paid'):
            order.status, order.accepted_at = 'preparing', timezone.now()
            minutes = prep_minutes if prep_minutes is not None else max(1, min(order.stall.prep_minutes, 180))
            order.estimated_ready_at = order.accepted_at + timedelta(minutes=minutes)
            order.prep_updated_at, order.prep_delay_reason = order.accepted_at, ''
        elif action == 'update_prep' and order.status == 'preparing' and not order.cancel_requested and (not delivery or order.payment_status == 'paid'):
            order.prep_updated_at = timezone.now()
            order.estimated_ready_at = order.prep_updated_at + timedelta(minutes=prep_minutes)
            order.prep_delay_reason = reason.strip()
        elif action == 'reject' and order.status == 'pending':
            if delivery and order.payment_status == 'paid':
                from .payments import queue_refund
                queue_refund(order, user, '商家无法接单，全额退款')
            order.status = 'rejected'
            order.cancel_reason = reason or '商家暂时无法接单'
            release_inventory(order)
        elif action == 'ready' and order.status == 'preparing' and not order.cancel_requested and (not delivery or order.payment_status == 'paid'):
            order.status, order.ready_at = 'ready', timezone.now()
        elif action == 'confirm_payment' and not delivery and order.status == 'ready' and not order.cancel_requested and order.payment_status == 'unpaid' and order.payment_method == 'offline':
            order.payment_status, order.paid_at = 'paid', timezone.now()
        elif action == 'dispatch' and delivery and order.status == 'ready' and order.payment_status == 'paid' and not order.cancel_requested:
            order.status, order.dispatched_at = 'delivering', timezone.now()
        elif action == 'arrive' and delivery and order.status == 'delivering' and order.payment_status == 'paid' and not order.cancel_requested:
            order.status, order.arrived_at = 'arrived', timezone.now()
        elif action == 'report_delivery_issue' and delivery and order.status in ('preparing', 'ready', 'delivering', 'arrived') and order.payment_status == 'paid' and not payment_busy(order):
            if not reason.strip(): raise BusinessError('请填写配送异常说明。', 'reason_required', status=400)
            order.delivery_issue = reason
            audit(user, 'delivery_issue_reported', order.pk, reason=reason)
        elif action == 'resolve_delivery_issue' and delivery and order.delivery_issue and order.status in ('preparing', 'ready', 'delivering', 'arrived') and order.payment_status == 'paid' and not payment_busy(order):
            if not reason.strip(): raise BusinessError('请填写配送异常解决说明。', 'reason_required', status=400)
            audit(user, 'delivery_issue_resolved', order.pk, issue=order.delivery_issue, resolution=reason)
            order.delivery_issue = ''
        elif action == 'complete' and order.status == ('arrived' if delivery else 'ready') and order.payment_status == 'paid' and not order.cancel_requested:
            if not secrets.compare_digest(str(code), order.pickup_code):
                raise BusinessError('取餐码不正确，请核对用户出示的取餐码。', 'invalid_pickup_code', status=400)
            order.status, order.completed_at = 'completed', timezone.now()
            Event.objects.create(type='order_completed', user=order.user, stall=order.stall)
        elif action == 'approve_cancel' and order.status in (('preparing', 'ready', 'delivering', 'arrived') if delivery else ('preparing', 'ready')) and order.cancel_requested and order.payment_status in (('unpaid', 'paid') if delivery else ('unpaid',)):
            if delivery and order.payment_status == 'paid':
                from .payments import queue_refund
                queue_refund(order, user, '商家同意取消，全额退款')
            order.status, order.cancel_requested = 'cancelled', False
            if not delivery: release_inventory(order)
        elif action == 'deny_cancel' and order.status in (('preparing', 'ready', 'delivering', 'arrived') if delivery else ('preparing', 'ready')) and order.cancel_requested:
            order.cancel_requested = False
            order.cancel_reason = reason or '商家未同意取消，请联系商家'
        else:
            raise BusinessError('订单状态已变化，当前无法执行此操作。', 'invalid_transition')
        order.save()
        if prep_action and idempotency_key:
            PreparationRequest.objects.create(order=order, idempotency_key=idempotency_key, request_hash=request_hash)
        audit(user, f'order_{action}', order.pk)
        return order


def confirm_receipt(order_id, user):
    with transaction.atomic():
        try: order = Order.objects.select_for_update().get(pk=order_id, user=user)
        except Order.DoesNotExist: raise BusinessError('订单不存在。', 'not_found', status=404) from None
        if order.mode == 'simulation':
            from .simulation import require_order_operation
            require_order_operation(order)
        if (order.fulfillment_type != 'delivery' or order.status != 'arrived'
                or order.payment_status != 'paid' or order.cancel_requested or order.delivery_issue or payment_busy(order)):
            raise BusinessError('订单到达交接点且无待处理售后时才能确认收餐。', 'invalid_transition')
        order.status, order.completed_at = 'completed', timezone.now()
        order.save(update_fields=['status', 'completed_at'])
        Event.objects.create(type='order_completed', user=user, stall=order.stall)
        audit(user, 'delivery_receipt_confirmed', order.pk)
        return order
