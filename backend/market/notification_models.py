"""Verified payment notifications without order foreign keys on the ingest path."""
import uuid

from django.db import models
from django.utils import timezone


class PaymentNotification(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    account_key = models.CharField(max_length=64)
    event_id = models.CharField(max_length=128)
    event_type = models.CharField(max_length=32)
    mchid = models.CharField(max_length=32)
    appid = models.CharField(max_length=32)
    resource = models.JSONField(default=dict)
    payload_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=12, default='pending', choices=[
        ('pending', '待处理'), ('processing', '处理中'), ('retry', '等待重试'), ('done', '已处理'), ('dead', '须人工处理')])
    attempts = models.PositiveIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now)
    lease_until = models.DateTimeField(null=True, blank=True)
    lease_token = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    processed_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=80, blank=True)
    conflict_count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['account_key', 'event_id'], name='unique_payment_notification')]
        indexes = [models.Index(fields=['status', 'available_at'], name='notification_work_idx')]
        verbose_name = verbose_name_plural = '支付通知收件箱'
