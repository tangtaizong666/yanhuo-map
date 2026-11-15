import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('market', '0027_four_digit_pickup_code'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='VerifiedAccountContact',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('phone_hash', models.CharField(max_length=64, unique=True)),
                ('masked_phone', models.CharField(max_length=20)),
                ('verified_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='verified_contact', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': '已验证注册手机',
                'verbose_name_plural': '已验证注册手机',
            },
        ),
    ]
