import re
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import migrations
from django.db.models import Q

SAFE_FILENAME_RE = re.compile(r'[A-Za-z0-9._-]+\.(?:jpg|jpeg|png|webp)\Z')


def migrate_legacy_public_payment_qr(apps, schema_editor):
    """Relocate exclusive legacy /media/merchants/<id>/<file> QRs into protected qr-* paths."""
    Stall = apps.get_model('market', 'Stall')
    Product = apps.get_model('market', 'Product')
    media_url = getattr(settings, 'MEDIA_URL', '/media/')

    for stall in Stall.objects.exclude(payment_qr_image='').iterator():
        raw = stall.payment_qr_image or ''
        expected_prefix = f'{media_url}merchants/{stall.pk}/'
        if not raw.startswith(expected_prefix) or '%' in raw or '\\' in raw:
            Stall.objects.filter(pk=stall.pk).update(payment_qr_image='')
            continue
        filename = raw[len(expected_prefix):]
        if '/' in filename or not SAFE_FILENAME_RE.fullmatch(filename):
            Stall.objects.filter(pk=stall.pk).update(payment_qr_image='')
            continue
        if filename.startswith('qr-'):
            rel_path = f'merchants/{stall.pk}/{filename}'
            if not default_storage.exists(rel_path):
                Stall.objects.filter(pk=stall.pk).update(payment_qr_image='')
            continue

        old_rel = f'merchants/{stall.pk}/{filename}'
        shared_publicly = (
            Stall.objects.filter(Q(image=raw) | Q(arrival_image=raw)).exists()
            or Product.objects.filter(image=raw).exists()
            or Stall.objects.exclude(pk=stall.pk).filter(payment_qr_image=raw).exists()
        )
        if shared_publicly or not default_storage.exists(old_rel):
            Stall.objects.filter(pk=stall.pk).update(payment_qr_image='')
            continue

        new_rel = f'merchants/{stall.pk}/qr-{filename}'
        new_url = f'{media_url}{new_rel}'
        try:
            if not default_storage.exists(new_rel):
                with default_storage.open(old_rel, 'rb') as src:
                    default_storage.save(new_rel, ContentFile(src.read()))
            if default_storage.exists(new_rel):
                Stall.objects.filter(pk=stall.pk).update(payment_qr_image=new_url)
                default_storage.delete(old_rel)
            else:
                Stall.objects.filter(pk=stall.pk).update(payment_qr_image='')
        except OSError:
            Stall.objects.filter(pk=stall.pk).update(payment_qr_image='')


class Migration(migrations.Migration):

    dependencies = [
        ('market', '0024_daily_quota_and_active_pickup_code'),
    ]

    operations = [
        migrations.RunPython(migrate_legacy_public_payment_qr, migrations.RunPython.noop),
    ]
