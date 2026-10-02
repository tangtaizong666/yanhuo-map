"""Replace only the original seeded image URLs, without reseeding business data."""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from market.models import Product, Stall
from .seed_demo import CATALOG, DEMO_PRODUCT_IMAGES, DEMO_STALL_IMAGES


class Command(BaseCommand):
    help = 'Refresh original demo image URLs only; preserve uploads, orders, prices, stock and accounts.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='List eligible changes without writing.')

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEMO_MODE or settings.PRODUCTION:
            raise CommandError('Demo asset refresh is disabled outside DEMO_MODE development.')

        count = 0
        for row in CATALOG:
            name, old_key, products = row[0], row[3], row[-1]
            original = f'/images/food-{old_key}.jpg'
            stalls = Stall.objects.filter(
                name=name, is_demo=True, merchant__user__username='vendor',
                merchant__license_number='DEMO-ONLY',
            )
            for stall in stalls:
                target = f'/images/{DEMO_STALL_IMAGES[name]}'
                if stall.image == original and original != target:
                    self.stdout.write(f'Stall {stall.pk}: {original} -> {target}')
                    count += 1
                    if not options['dry_run']:
                        # Compare-and-swap preserves a concurrent merchant photo upload.
                        Stall.objects.filter(pk=stall.pk, image=original).update(image=target)
                for index, (product_name, _description, _price) in enumerate(products):
                    target = f'/images/{DEMO_PRODUCT_IMAGES[name][index]}'
                    if target == original:
                        continue
                    eligible = Product.objects.filter(stall=stall, name=product_name, image=original)
                    for product in eligible:
                        self.stdout.write(f'Product {product.pk}: {original} -> {target}')
                        count += 1
                        if not options['dry_run']:
                            Product.objects.filter(pk=product.pk, image=original).update(image=target)

        verb = 'Eligible image changes' if options['dry_run'] else 'Image changes attempted'
        self.stdout.write(self.style.SUCCESS(f'{verb}: {count}. All non-image fields and historical order snapshots preserved.'))
