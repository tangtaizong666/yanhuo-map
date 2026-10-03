import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


def existing_successes(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        # Django emits deferred FK indexes when the schema editor exits. Validate
        # FK events immediately during backfill, before those index statements.
        schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE')
    Refund = apps.get_model('market', 'PaymentRefund')
    Refund.objects.filter(status='success').update(resolved_at=models.F('completed_at'))
    for refund in Refund.objects.select_related('payment').iterator():
        Refund.objects.filter(pk=refund.pk).update(mchid=refund.payment.mchid)


class Migration(migrations.Migration):
    dependencies = [('market', '0012_security'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.AlterField(model_name='paymentrefund', name='payment', field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='refunds', to='market.paymentattempt')),
        migrations.AlterField(model_name='paymentrefund', name='order', field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='refunds', to='market.order')),
        migrations.AlterField(model_name='paymentrefund', name='out_refund_no', field=models.CharField(max_length=64)),
        migrations.AddField(model_name='paymentrefund', name='mchid', field=models.CharField(default='', max_length=32, verbose_name='原收款商户号'), preserve_default=False),
        migrations.AlterModelOptions(name='paymentrefund', options={'ordering': ['-created_at', '-id'], 'verbose_name': '微信退款记录', 'verbose_name_plural': '微信退款记录'}),
        migrations.AddField(model_name='paymentrefund', name='resolved_at', field=models.DateTimeField(blank=True, null=True, verbose_name='核验结案时间')),
        migrations.AddField(model_name='paymentrefund', name='replaces', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='retries', to='market.paymentrefund')),
        migrations.AddField(model_name='paymentrefund', name='source', field=models.CharField(choices=[('application', '订单申请'), ('retry', '运营重试'), ('external', '外部退款核验'), ('compensation', '异常付款补偿')], default='application', max_length=16)),
        migrations.AddField(model_name='paymentrefund', name='operation_key', field=models.CharField(blank=True, max_length=128, null=True, unique=True)),
        migrations.AddField(model_name='paymentrefund', name='operation_hash', field=models.CharField(blank=True, max_length=64)),
        migrations.AddConstraint(model_name='paymentrefund', constraint=models.UniqueConstraint(condition=models.Q(status__in=['creating', 'processing', 'reconcile', 'abnormal']), fields=('payment',), name='one_active_refund_per_payment')),
        migrations.AddConstraint(model_name='paymentrefund', constraint=models.UniqueConstraint(fields=('mchid', 'out_refund_no'), name='unique_merchant_refund_number')),
        migrations.CreateModel(name='FinancialEvidence', fields=[
            ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ('operation', models.CharField(max_length=40)),
            ('outcome', models.CharField(max_length=24)),
            ('reason', models.CharField(max_length=200)),
            ('facts', models.JSONField(default=dict)),
            ('facts_hash', models.CharField(max_length=64)),
            ('created_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
            ('actor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
            ('order', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='financial_evidence', to='market.order')),
            ('payment', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='financial_evidence', to='market.paymentattempt')),
            ('refund', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='financial_evidence', to='market.paymentrefund')),
        ], options={'ordering': ['-created_at', '-id'], 'verbose_name': '资金核验证据', 'verbose_name_plural': '资金核验证据',
                    'default_permissions': ('view',), 'permissions': [('resolve_payments', '核验及处理异常付款退款'), ('export_financial_reconciliation', '导出商户资金对账清单')]}),
        # PostgreSQL defers existing FK checks during data updates. Keep data
        # writes after all DDL so adding constraints cannot hit pending triggers.
        migrations.RunPython(existing_successes, migrations.RunPython.noop),
    ]
