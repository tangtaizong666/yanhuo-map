from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from market.models import Stall
from market.simulation import enabled, prepare_stall


class Command(BaseCommand):
    help = '幂等准备示例摊位的模拟交接点；不修改真实资质、营业状态或确认位置。'

    def add_arguments(self, parser):
        parser.add_argument('--enable-services', action='store_true')

    def handle(self, *args, **options):
        if not enabled(): raise CommandError('需要非生产 DEMO_MODE 和 SERVICES_SIMULATION_ENABLED=true。')
        count = 0
        for stall_id in Stall.objects.filter(is_demo=True, transaction_enabled=True, merchant__is_verified=True).values_list('pk', flat=True):
            with transaction.atomic():
                stall = Stall.objects.select_for_update(no_key=True).get(pk=stall_id)
                prepare_stall(stall)
                if options['enable_services']:
                    stall.simulation_payment_enabled = stall.simulation_delivery_enabled = True
                    stall.save(update_fields=['simulation_payment_enabled', 'simulation_delivery_enabled'])
                count += 1
        self.stdout.write(f'simulation_stalls_prepared={count}; real_payment_and_delivery_approvals_unchanged')
