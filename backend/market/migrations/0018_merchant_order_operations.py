from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [('market', '0017_discovery_privacy')]
    operations = [migrations.CreateModel(
        name='MerchantOrderOperation',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('idempotency_key', models.CharField(max_length=128)),
            ('action', models.CharField(max_length=32)),
            ('request_hash', models.CharField(max_length=64)),
            ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
            ('order', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='merchant_operations', to='market.order')),
        ], options={'constraints': [models.UniqueConstraint(fields=('order', 'idempotency_key'), name='unique_merchant_order_operation')]})]
