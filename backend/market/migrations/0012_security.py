import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('market', '0011_counter_operations')]

    operations = [
        migrations.CreateModel(
            name='AuthenticationFailureBucket',
            fields=[
                ('key', models.CharField(max_length=64, primary_key=True, serialize=False)),
                ('failures', models.PositiveIntegerField(default=0)),
                ('window_started_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('updated_at', models.DateTimeField(default=django.utils.timezone.now, db_index=True)),
            ],
        ),
        migrations.CreateModel(
            name='ProductCreation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('idempotency_key', models.CharField(max_length=128)),
                ('request_hash', models.CharField(max_length=64)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('product', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to='market.product')),
                ('stall', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='market.stall')),
            ],
            options={'constraints': [models.UniqueConstraint(fields=('stall', 'idempotency_key'), name='unique_product_creation_key')]},
        ),
    ]
