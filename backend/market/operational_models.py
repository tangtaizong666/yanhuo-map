from django.db import models


class WorkerHeartbeat(models.Model):
    """A durable progress signal, independent from the HTTP process health."""

    name = models.CharField(max_length=40, primary_key=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_failure_at = models.DateTimeField(null=True, blank=True)
    processed = models.PositiveIntegerField(default=0)
    failure_count = models.PositiveBigIntegerField(default=0)
    last_error_code = models.CharField(max_length=80, blank=True)

    class Meta:
        verbose_name = verbose_name_plural = '后台任务运行状态'
