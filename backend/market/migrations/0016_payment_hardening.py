import uuid

from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [('market', '0015_order_history_indexes')]
    operations = [
        migrations.AddField(model_name='paymentattempt', name='next_query_at', field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name='paymentattempt', name='consecutive_query_failures', field=models.PositiveSmallIntegerField(default=0)),
        migrations.AddField(model_name='paymentrefund', name='next_query_at', field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name='paymentrefund', name='consecutive_query_failures', field=models.PositiveSmallIntegerField(default=0)),
        migrations.CreateModel(name='PaymentNotification', fields=[
            ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ('account_key', models.CharField(max_length=64)),
            ('event_id', models.CharField(max_length=128)),
            ('event_type', models.CharField(max_length=32)),
            ('mchid', models.CharField(max_length=32)),
            ('appid', models.CharField(max_length=32)),
            ('resource', models.JSONField(default=dict)),
            ('payload_hash', models.CharField(max_length=64)),
            ('status', models.CharField(choices=[('pending', '待处理'), ('processing', '处理中'), ('retry', '等待重试'), ('done', '已处理'), ('dead', '须人工处理')], default='pending', max_length=12)),
            ('attempts', models.PositiveIntegerField(default=0)),
            ('available_at', models.DateTimeField(default=django.utils.timezone.now)),
            ('lease_until', models.DateTimeField(blank=True, null=True)),
            ('lease_token', models.UUIDField(blank=True, null=True)),
            ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
            ('processed_at', models.DateTimeField(blank=True, null=True)),
            ('last_error_code', models.CharField(blank=True, max_length=80)),
            ('conflict_count', models.PositiveIntegerField(default=0)),
        ], options={
            'verbose_name': '支付通知收件箱', 'verbose_name_plural': '支付通知收件箱',
            'indexes': [models.Index(fields=['status', 'available_at'], name='notification_work_idx')],
            'constraints': [models.UniqueConstraint(fields=('account_key', 'event_id'), name='unique_payment_notification')],
        }),
    ]
