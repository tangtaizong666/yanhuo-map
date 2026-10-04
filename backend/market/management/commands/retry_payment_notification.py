import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from market.notification_models import PaymentNotification
from market.services import audit


class Command(BaseCommand):
    help = '记录原因并重新排队一条失败通知；保留原已验证资金事实和重试历史。'

    def add_arguments(self, parser):
        parser.add_argument('notification_id', type=uuid.UUID)
        parser.add_argument('--reason', required=True)
        parser.add_argument('--acknowledge-conflict', action='store_true',
            help='仅记录已核验的重复事件冲突，不重放或修改原资金事实。')

    def handle(self, *args, **options):
        reason = options['reason'].strip()
        if not reason or len(reason) > 200:
            raise CommandError('请填写1至200字的重试原因。')
        with transaction.atomic():
            row = PaymentNotification.objects.select_for_update().filter(pk=options['notification_id']).first()
            if options['acknowledge_conflict']:
                if row is None or not row.conflict_count:
                    raise CommandError('此通知没有待核验的事件冲突。')
                audit(None, 'payment_notification_conflict_ack', row.pk, reason=reason,
                    conflict_count=row.conflict_count, payload_hash=row.payload_hash)
                row.conflict_count = 0
                row.save(update_fields=['conflict_count'])
                self.stdout.write(f'payment_notification_conflict_acknowledged={row.pk}')
                return
            if row is None or row.status not in ('dead', 'retry'):
                raise CommandError('只能重新排队失败或等待重试的通知。')
            row.status, row.available_at = 'pending', timezone.now()
            row.lease_until = row.lease_token = None
            row.save(update_fields=['status', 'available_at', 'lease_until', 'lease_token'])
            audit(None, 'payment_notification_retry', row.pk, reason=reason, previous_attempts=row.attempts)
        self.stdout.write(f'payment_notification_queued={row.pk}')
