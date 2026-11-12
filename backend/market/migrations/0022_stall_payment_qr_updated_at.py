from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("market", "0021_admission_and_display_supply"),
    ]

    operations = [
        migrations.AddField(
            model_name="stall",
            name="payment_qr_updated_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="收款码更新时间"),
        ),
    ]
