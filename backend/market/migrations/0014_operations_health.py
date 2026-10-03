from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('market', '0013_financial_resolution')]
    operations = [migrations.CreateModel(
        name='WorkerHeartbeat',
        fields=[
            ('name', models.CharField(max_length=40, primary_key=True, serialize=False)),
            ('last_attempt_at', models.DateTimeField(blank=True, null=True)),
            ('last_success_at', models.DateTimeField(blank=True, null=True)),
            ('last_failure_at', models.DateTimeField(blank=True, null=True)),
            ('processed', models.PositiveIntegerField(default=0)),
            ('failure_count', models.PositiveBigIntegerField(default=0)),
            ('last_error_code', models.CharField(blank=True, max_length=80)),
        ],
        options={'verbose_name': '后台任务运行状态', 'verbose_name_plural': '后台任务运行状态'},
    )]
