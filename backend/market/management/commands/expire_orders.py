import time
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from market.models import Event, Order, Stall
from market.services import expire_pending_orders


class Command(BaseCommand):
    help = 'Cancel unaccepted orders and record operational anomalies. --loop runs every 15 seconds.'
    def add_arguments(self, parser):
        parser.add_argument('--loop', action='store_true')
        parser.add_argument('--interval', type=int, default=15)
    def handle(self, *args, **options):
        while True:
            expired = expire_pending_orders()
            if expired: self.stdout.write(f'Expired {expired} unaccepted orders.')
            for stall in Stall.objects.select_related('current_session').filter(is_visible=True):
                if stall.effective_status() == 'stale':
                    Event.objects.get_or_create(type='stall_stale', stall=stall,
                        metadata={'session_id': stall.current_session_id, 'confirmed': stall.current_session.last_confirmed_at.isoformat()})
            for order in Order.objects.filter(status='ready', ready_at__lt=timezone.now()-timedelta(hours=1)):
                Event.objects.get_or_create(type='order_uncollected', stall=order.stall, user=order.user, metadata={'order_id': str(order.pk)})
            if not options['loop']: break
            time.sleep(max(5, min(options['interval'], 60)))
