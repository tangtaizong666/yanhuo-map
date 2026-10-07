import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections, connection

from market.notification_inbox import process_notifications
from market.runtime_health import record_worker_failure, record_worker_success


class NotificationProcessingFailed(Exception):
    pass


class Command(BaseCommand):
    help = '处理已验证支付通知；只领取本地持久收件箱，不调用微信网关。'

    def add_arguments(self, parser):
        parser.add_argument('--loop', action='store_true')
        parser.add_argument('--interval', type=float, default=1)
        parser.add_argument('--limit', type=int, default=100)

    def handle(self, *args, **options):
        while True:
            try:
                if not connection.in_atomic_block:
                    close_old_connections()
                processed, failures = process_notifications(limit=options['limit'])
                if failures:
                    record_worker_failure('process_payment_notifications', NotificationProcessingFailed())
                else:
                    record_worker_success('process_payment_notifications', processed)
                if processed or failures or not options['loop']:
                    self.stdout.write(f'payment_notifications_processed={processed} failed={failures}')
            except Exception as exc:
                record_worker_failure('process_payment_notifications', exc)
                if not options['loop']:
                    raise
            if not options['loop']:
                break
            time.sleep(max(0.1, min(options['interval'], 60)))
