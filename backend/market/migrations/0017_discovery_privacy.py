from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('market', '0016_payment_hardening')]
    operations = [migrations.AddField(
        model_name='stall', name='public_phone_enabled',
        field=models.BooleanField('允许公开联系电话', default=False),
    )]
