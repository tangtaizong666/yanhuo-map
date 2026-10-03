"""Operator reconciliation export: receipts and refunds remain separate rows."""
import csv
import io
from datetime import datetime, time, timedelta

from django.db.models import Exists, OuterRef, Q
from django.http import StreamingHttpResponse
from django.utils import timezone

from .errors import BusinessError
from .models import Order, PaymentAttempt, PaymentRefund
from .reconciliation import require_operator
from .services import audit
from .financial import hold_reason


HEADERS = ['record_type', 'merchant_id', 'order_number', 'order_id', 'account_key', 'out_trade_no',
    'transaction_id', 'out_refund_no', 'refund_id', 'amount_cents', 'currency', 'provider_state',
    'order_payment_status', 'review_required', 'resolved_at', 'occurred_at', 'reason']


def _csv_row(values):
    output = io.StringIO()
    # Identifiers are normally constrained, but exports must also be safe when a
    # legacy/admin-entered value begins with an Excel formula prefix.
    cells = []
    for value in values:
        value = '' if value is None else str(value)
        cells.append("'" + value if value.startswith(('=', '+', '-', '@', '\t', '\r')) else value)
    csv.writer(output).writerow(cells)
    return output.getvalue()


def reconciliation_export(actor, merchant_id, starts_on, ends_on):
    require_operator(actor, 'market.export_financial_reconciliation')
    if ends_on < starts_on or (ends_on - starts_on).days > 30:
        raise BusinessError('每次导出请选择最多31天的日期范围。', 'invalid_date_range', status=400)
    start = timezone.make_aware(datetime.combine(starts_on, time.min))
    end = timezone.make_aware(datetime.combine(ends_on + timedelta(days=1), time.min))
    payments = PaymentAttempt.objects.filter(merchant_id=merchant_id, mode='live').filter(
        Q(paid_at__gte=start, paid_at__lt=end) | Q(paid_at__isnull=True, created_at__gte=start, created_at__lt=end)
    ).select_related('order').order_by('created_at', 'pk')
    refunds = PaymentRefund.objects.filter(payment__merchant_id=merchant_id, mode='live').filter(
        Q(completed_at__gte=start, completed_at__lt=end) | Q(completed_at__isnull=True, created_at__gte=start, created_at__lt=end)
    ).select_related('order', 'payment').order_by('created_at', 'pk')
    offline = Order.objects.filter(stall__merchant_id=merchant_id, mode='live', payment_method='offline',
        payment_status__in=('paid', 'refunding', 'refunded'), paid_at__gte=start, paid_at__lt=end).order_by('paid_at', 'pk')
    differences = Order.objects.filter(stall__merchant_id=merchant_id, mode='live').annotate(
        _unresolved_refund=Exists(PaymentRefund.objects.filter(order_id=OuterRef('pk'), resolved_at__isnull=True).exclude(status='success')),
        _active_payment=Exists(PaymentAttempt.objects.filter(order_id=OuterRef('pk'), status__in=PaymentAttempt.ACTIVE_STATUSES))
    ).filter(Q(payment_review_required=True) | Q(payment_status='refunding') | Q(_unresolved_refund=True) | Q(_active_payment=True)
    ).prefetch_related('payments', 'refunds').order_by('created_at', 'pk')
    audit(actor, 'financial_reconciliation_exported', merchant_id, starts_on=starts_on.isoformat(), ends_on=ends_on.isoformat())
    def rows():
        yield '\ufeff' + _csv_row(HEADERS)
        for p in payments.iterator(chunk_size=500):
            yield _csv_row(['wechat_payment', merchant_id, p.order.number, p.order_id, p.account_key, p.out_trade_no,
                p.transaction_id, '', '', p.amount_cents, p.currency, p.status, p.order.payment_status,
                p.order.payment_review_required, '', (p.paid_at or p.created_at).isoformat(), ''])
        for r in refunds.iterator(chunk_size=500):
            yield _csv_row(['wechat_refund', merchant_id, r.order.number, r.order_id, r.payment.account_key,
                r.payment.out_trade_no, r.payment.transaction_id, r.out_refund_no, r.refund_id, r.amount_cents,
                r.payment.currency, r.status, r.order.payment_status, r.order.payment_review_required,
                r.resolved_at.isoformat() if r.resolved_at else '', (r.completed_at or r.created_at).isoformat(), ''])
        for o in offline.iterator(chunk_size=500):
            yield _csv_row(['offline_confirmation', merchant_id, o.number, o.pk, '', '', '', '', '', o.total_cents,
                'CNY', 'merchant_confirmed', o.payment_status, o.payment_review_required, '', o.paid_at.isoformat(), ''])
        for o in differences.iterator(chunk_size=500):
            yield _csv_row(['unresolved_difference', merchant_id, o.number, o.pk, '', '', '', '', '', o.total_cents,
                'CNY', '', o.payment_status, o.payment_review_required, '', o.created_at.isoformat(), hold_reason(o)])
    response = StreamingHttpResponse(rows(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="reconciliation-{merchant_id}-{starts_on}-{ends_on}.csv"'
    response['Cache-Control'] = 'no-store, private'
    return response
