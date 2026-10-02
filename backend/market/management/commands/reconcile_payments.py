import time
from datetime import datetime, timezone
from django.core.management.base import BaseCommand
from django.db.models import F
from market.models import PaymentAttempt, PaymentRefund
from market.payments import sync_payment


class Command(BaseCommand):
    help = '查询在途微信支付与退款；本地时间到期不会作为已关单或已退款依据。'

    def add_arguments(self, parser):
        parser.add_argument('--loop', action='store_true')
        parser.add_argument('--interval', type=int, default=30)
        parser.add_argument('--limit', type=int, default=100)

    def handle(self, *args, **options):
        interval, limit = max(10, options['interval']), max(1, min(1000, options['limit']))
        while True:
            # Oldest checked first avoids starving uncertainty behind newer orders.
            pending = PaymentAttempt.objects.filter(status__in=PaymentAttempt.ACTIVE_STATUSES).order_by(F('last_checked_at').asc(nulls_first=True)).values_list('order_id', 'last_checked_at')
            refunds = PaymentRefund.objects.filter(status__in=['creating', 'processing', 'reconcile', 'abnormal']).order_by(F('last_checked_at').asc(nulls_first=True)).values_list('order_id', 'last_checked_at')
            candidates = sorted([*list(refunds[:limit]), *list(pending[:limit])],
                key=lambda item: item[1] or datetime.min.replace(tzinfo=timezone.utc))
            ids = list(dict.fromkeys(item[0] for item in candidates))[:limit]
            for order_id in ids:
                try:
                    sync_payment(order_id)
                except Exception:
                    # No exception details or secrets in the worker log; the durable record remains retryable.
                    self.stderr.write(f'payment_reconcile_failed order={order_id}')
            self.stdout.write(f'payment_reconcile_checked={len(ids)}')
            if not options['loop']: break
            time.sleep(interval)
