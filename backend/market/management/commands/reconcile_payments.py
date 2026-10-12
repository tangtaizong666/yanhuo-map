import time
from datetime import datetime, timezone
from django.core.management.base import BaseCommand
from django.db import close_old_connections, connection
from django.db.models import F, Q
from django.utils import timezone as django_timezone
from market.models import PaymentAttempt, PaymentRefund
from market.payments import sync_payment
from market.runtime_health import record_worker_failure, record_worker_success


class ReconciliationUnverified(Exception):
    """Only the class name reaches the worker health record."""


class Command(BaseCommand):
    help = '查询在途微信支付与退款；本地时间到期不会作为已关单或已退款依据。'

    def add_arguments(self, parser):
        parser.add_argument('--loop', action='store_true')
        parser.add_argument('--interval', type=int, default=30)
        parser.add_argument('--limit', type=int, default=100)

    def handle(self, *args, **options):
        interval, limit = max(10, options['interval']), max(1, min(1000, options['limit']))
        while True:
            try:
                self.reconcile_batch(limit)
            except Exception as exc:
                record_worker_failure('reconcile_payments', exc)
                self.stderr.write('payment_reconcile_batch_failed')
            if not options['loop']: break
            time.sleep(interval)

    def reconcile_batch(self, limit):
        # call_command can run inside an existing transaction (including a
        # caller's PostgreSQL transaction); only own our worker connections.
        if not connection.in_atomic_block:
            close_old_connections()
        failed = False
        # Oldest checked first avoids starving uncertainty behind newer orders.
        available = Q(request_in_flight_until__isnull=True) | Q(request_in_flight_until__lte=django_timezone.now())
        available &= Q(next_query_at__isnull=True) | Q(next_query_at__lte=django_timezone.now())
        pending = PaymentAttempt.objects.filter(Q(status__in=PaymentAttempt.ACTIVE_STATUSES) | Q(status='paid', order__payment_review_required=True)).filter(available)
        refunds = PaymentRefund.objects.filter(resolved_at__isnull=True).exclude(status='success').filter(available)
        from market.simulation import enabled
        if not enabled():
            pending, refunds = pending.filter(mode='live'), refunds.filter(mode='live')
        pending = pending.order_by(F('last_checked_at').asc(nulls_first=True), 'pk').values_list('order_id', 'last_checked_at')
        refunds = refunds.order_by(F('last_checked_at').asc(nulls_first=True), 'pk').values_list('order_id', 'last_checked_at')
        candidates = sorted([*list(refunds[:limit]), *list(pending[:limit])],
            key=lambda item: item[1] or datetime.min.replace(tzinfo=timezone.utc))
        ids = list(dict.fromkeys(item[0] for item in candidates))[:limit]
        for order_id in ids:
            try:
                sync_payment(order_id)
                if (PaymentAttempt.objects.filter(Q(status__in=PaymentAttempt.ACTIVE_STATUSES) | Q(status='paid', order__payment_review_required=True), order_id=order_id).exclude(error_code='').exists()
                        or PaymentRefund.objects.filter(order_id=order_id, resolved_at__isnull=True).exclude(error_code='').exists()):
                    raise ReconciliationUnverified()
            except Exception as exc:
                failed = True
                record_worker_failure('reconcile_payments', exc)
                # No exception details or secrets in the worker log; the durable record remains retryable.
                self.stderr.write(f'payment_reconcile_failed order={order_id}')
        self.stdout.write(f'payment_reconcile_checked={len(ids)}')
        if not failed: record_worker_success('reconcile_payments', len(ids))
