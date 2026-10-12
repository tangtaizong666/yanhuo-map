from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('market', '0018_merchant_order_operations')]
    operations = [
        migrations.AddField(model_name='feedback', name='verification',
            field=models.CharField(choices=[('unreviewed', '尚未核实'), ('confirmed', '已核实属实'), ('dismissed', '核实未成立')],
                                   default='unreviewed', max_length=16, verbose_name='位置反馈核实结果')),
        migrations.AddField(model_name='feedback', name='verification_note',
            field=models.CharField(blank=True, max_length=500, verbose_name='运营核实记录（不公开）')),
    ]
