import logging
import re
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import migrations
from django.db.models import Q

logger = logging.getLogger(__name__)
SAFE_FILENAME_RE = re.compile(r'[A-Za-z0-9._-]+\.(?:jpg|jpeg|png|webp)\Z')
QR_FILENAME_RE = re.compile(r'qr-[A-Za-z0-9._-]+\.(?:jpg|jpeg|png|webp)\Z')


def migrate_legacy_public_payment_qr(apps, schema_editor):
    """Copy exclusive legacy /media/merchants/<id>/<file> QRs to protected qr-* and delete old files post-commit."""
    Stall = apps.get_model('market', 'Stall')
    Product = apps.get_model('market', 'Product')
    media_url = getattr(settings, 'MEDIA_URL', '/media/')
    pending_old_deletes = []

    for stall in Stall.objects.exclude(payment_qr_image='').iterator():
        raw = stall.payment_qr_image or ''
        expected_prefix = f'{media_url}merchants/{stall.pk}/'
        if not raw.startswith(expected_prefix) or '%' in raw or '\\' in raw:
            logger.warning('Clearing non-canonical legacy payment_qr_image on stall %s', stall.pk)
            Stall.objects.filter(pk=stall.pk).update(payment_qr_image='')
            continue
        filename = raw[len(expected_prefix):]
        if '/' in filename or not SAFE_FILENAME_RE.fullmatch(filename):
            logger.warning('Clearing invalid-filename legacy payment_qr_image on stall %s', stall.pk)
            Stall.objects.filter(pk=stall.pk).update(payment_qr_image='')
            continue
        if filename.startswith('qr-'):
            rel_path = f'merchants/{stall.pk}/{filename}'
            if not QR_FILENAME_RE.fullmatch(filename) or not default_storage.exists(rel_path):
                logger.warning('Clearing missing protected payment_qr_image on stall %s', stall.pk)
                Stall.objects.filter(pk=stall.pk).update(payment_qr_image='')
            continue

        old_rel = f'merchants/{stall.pk}/{filename}'
        shared_publicly = (
            Stall.objects.filter(Q(image=raw) | Q(arrival_image=raw)).exists()
            or Product.objects.filter(image=raw).exists()
            or Stall.objects.exclude(pk=stall.pk).filter(payment_qr_image=raw).exists()
        )
        if shared_publicly or not default_storage.exists(old_rel):
            logger.warning(
                'Clearing %s legacy payment_qr_image on stall %s',
                'shared' if shared_publicly else 'missing',
                stall.pk,
            )
            Stall.objects.filter(pk=stall.pk).update(payment_qr_image='')
            continue

        target_rel = f'merchants/{stall.pk}/qr-{filename}'
        try:
            with default_storage.open(old_rel, 'rb') as src:
                payload_bytes = src.read()
            saved_rel = default_storage.save(target_rel, ContentFile(payload_bytes))
            saved_name = saved_rel.rsplit('/', 1)[-1]
            if (
                saved_rel.startswith(f'merchants/{stall.pk}/')
                and QR_FILENAME_RE.fullmatch(saved_name)
                and default_storage.exists(saved_rel)
            ):
                Stall.objects.filter(pk=stall.pk).update(payment_qr_image=f'{media_url}{saved_rel}')
                pending_old_deletes.append((raw, old_rel))
            else:
                logger.warning('Saved QR path %s did not validate for stall %s; clearing', saved_rel, stall.pk)
                Stall.objects.filter(pk=stall.pk).update(payment_qr_image='')
        except OSError:
            logger.warning('Failed to migrate legacy payment_qr_image for stall %s', stall.pk, exc_info=True)
            Stall.objects.filter(pk=stall.pk).update(payment_qr_image='')

    if pending_old_deletes:
        def _delete_migrated_public_files(items=tuple(pending_old_deletes)):
            for old_url, rel_path in items:
                try:
                    if (
                        Stall.objects.filter(
                            Q(payment_qr_image=old_url) | Q(image=old_url) | Q(arrival_image=old_url)
                        ).exists()
                        or Product.objects.filter(image=old_url).exists()
                    ):
                        continue
                    default_storage.delete(rel_path)
                except Exception:
                    logger.warning('Failed post-commit deletion of migrated QR %s', rel_path, exc_info=True)

        schema_editor.connection.on_commit(_delete_migrated_public_files)


class Migration(migrations.Migration):

    dependencies = [
        ('market', '0024_daily_quota_and_active_pickup_code'),
    ]

    operations = [
        migrations.RunPython(migrate_legacy_public_payment_qr, migrations.RunPython.noop),
    ]
