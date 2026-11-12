from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("market", "0022_stall_payment_qr_updated_at"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="auditlog",
            index=models.Index(fields=["target", "-created_at"], name="audit_target_time_idx"),
        ),
    ]
