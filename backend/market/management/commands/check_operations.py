import json

from django.core.management.base import BaseCommand, CommandError
from market.runtime_health import WORKERS, operations_status


class Command(BaseCommand):
    help = '只读检查任务心跳与真实支付积压；异常时返回非零退出码，供监控调用。'

    def add_arguments(self, parser):
        parser.add_argument('--worker', choices=WORKERS)
        parser.add_argument('--max-heartbeat-age', type=int, default=120)
        parser.add_argument('--max-payment-age', type=int, default=600)

    def handle(self, *args, **options):
        result = operations_status(worker=options['worker'],
            max_heartbeat_age=max(15, options['max_heartbeat_age']),
            max_payment_age=max(60, options['max_payment_age']))
        self.stdout.write(json.dumps(result, ensure_ascii=False))
        if result['status'] != 'ok':
            raise CommandError('后台任务或支付处理需要关注；请查看上面的诊断。')
