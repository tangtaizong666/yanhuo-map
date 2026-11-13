import time
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.db import close_old_connections, connection
from django.utils import timezone
from market.models import Event, Order, SiteConfiguration, Stall
from market.services import expire_pending_orders
from market.runtime_health import record_worker_failure, record_worker_success
from market.security_cleanup import cleanup_authentication_buckets


class Command(BaseCommand):
    help = 'Cancel unaccepted orders and record operational anomalies. --loop runs every 15 seconds.'
    def add_arguments(self, parser):
        parser.add_argument('--loop', action='store_true')
        parser.add_argument('--interval', type=int, default=15)
        parser.add_argument('--batch-size', type=int, default=100)
    def handle(self, *args, **options):
        while True:
            if not connection.in_atomic_block:
                close_old_connections()
            try:
                failures = []
                expired = expire_pending_orders(limit=max(1, min(options['batch_size'], 1000)), on_error=failures.append)
                cleanup_authentication_buckets()
                config = SiteConfiguration.current()
                for stall in Stall.objects.select_related('current_session').filter(is_visible=True).iterator(chunk_size=100):
                    if stall.effective_status(config) == 'stale':
                        confirmed = (stall.current_session.last_confirmed_at.isoformat()
                            if stall.current_session and stall.current_session.last_confirmed_at else None)
                        stale_meta = {'session_id': stall.current_session_id, 'confirmed': confirmed}
                        if not Event.objects.filter(type='stall_stale', stall=stall, metadata=stale_meta).exists():
                            Event.objects.create(type='stall_stale', stall=stall, metadata=stale_meta)
                now = timezone.now()
                for order in Order.objects.filter(
                    status='ready',
                    ready_at__gte=now - timedelta(days=2),
                    ready_at__lt=now - timedelta(hours=1),
                ).iterator(chunk_size=100):
                    uncollected_meta = {'order_id': str(order.pk)}
                    if not Event.objects.filter(type='order_uncollected', stall_id=order.stall_id,
                            user_id=order.user_id, metadata=uncollected_meta).exists():
                        Event.objects.create(type='order_uncollected', stall_id=order.stall_id,
                            user_id=order.user_id, metadata=uncollected_meta)
                if failures:
                    for failure in failures:
                        record_worker_failure('expire_orders', failure)
                else:
                    record_worker_success('expire_orders', expired)
                self.stdout.write(f'expire_orders_processed={expired}')
            except Exception as exc:
                record_worker_failure('expire_orders', exc)
                if not options['loop']:
                    raise
            if not options['loop']: break
            time.sleep(max(5, min(options['interval'], 60)))
