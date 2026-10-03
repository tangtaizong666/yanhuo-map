from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('market', '0014_operations_health')]
    operations = [
        migrations.AddIndex(model_name='order', index=models.Index(fields=['user', '-created_at', '-id'], name='order_user_history_idx')),
        migrations.AddIndex(model_name='order', index=models.Index(fields=['stall', '-created_at', '-id'], name='order_stall_history_idx')),
    ]
