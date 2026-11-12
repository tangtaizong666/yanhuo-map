from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from market.models import Event

# Only client-reported browsing analytics. Order and stall events written by the
# server feed pilot evidence and de-duplicated anomaly records, so they are kept.
BROWSING_TYPES = ('browse', 'home_view', 'stall_view', 'map_view', 'checkout_view', 'location_denied',
    'map_error', 'api_error', 'share_click', 'share_open', 'qr_open', 'route_click', 'reorder')


class Command(BaseCommand):
    help = 'Delete browsing analytics events older than --days (default 180). Use --dry-run to count only.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=180)
        parser.add_argument('--batch-size', type=int, default=1000)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        if options['days'] < 30:
            raise CommandError('--days must be at least 30.')
        query = Event.objects.filter(type__in=BROWSING_TYPES, created_at__lt=timezone.now() - timedelta(days=options['days']))
        if options['dry_run']:
            self.stdout.write(f'would_delete={query.count()}')
            return
        deleted, size = 0, max(1, min(options['batch_size'], 10000))
        while True:
            ids = list(query.order_by('pk').values_list('pk', flat=True)[:size])
            if not ids: break
            deleted += Event.objects.filter(pk__in=ids).delete()[0]
        self.stdout.write(f'deleted={deleted}')
