from django.core.management.base import BaseCommand, CommandError

from market.demo_keepalive import (
    demo_environment_active,
    recover_startup_demo_stalls,
    restore_demo_stalls,
)


class Command(BaseCommand):
    help = 'Refresh or restore visible demo stall sessions in non-production DEMO_MODE.'

    def add_arguments(self, parser):
        parser.add_argument('--startup', action='store_true')
        parser.add_argument('--clear-seeded-closing', action='store_true')

    def handle(self, *args, **options):
        if not demo_environment_active():
            if options['startup']:
                self.stdout.write('demo_stalls_refreshed=0 (demo_mode_inactive)')
                return
            raise CommandError('refresh_demo_stalls is only permitted in non-production DEMO_MODE.')
        if options['startup']:
            count = recover_startup_demo_stalls(
                clear_seeded_closing=bool(options['clear_seeded_closing']),
            )
            self.stdout.write(f'demo_stalls_refreshed={count}')
            return
        result = restore_demo_stalls()
        self.stdout.write(f"demo_stalls_restored={result['restored_count']}")
