"""Append-only, redacted evidence for operator financial decisions."""
import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class EvidenceQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('资金核验证据不可修改。')

    def delete(self):
        raise ValidationError('资金核验证据不可删除。')

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError('资金核验证据不可修改。')


class FinancialEvidence(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey('market.Order', on_delete=models.PROTECT, related_name='financial_evidence')
    payment = models.ForeignKey('market.PaymentAttempt', null=True, blank=True, on_delete=models.PROTECT, related_name='financial_evidence')
    refund = models.ForeignKey('market.PaymentRefund', null=True, blank=True, on_delete=models.PROTECT, related_name='financial_evidence')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)
    operation = models.CharField(max_length=40)
    outcome = models.CharField(max_length=24)
    reason = models.CharField(max_length=200)
    facts = models.JSONField(default=dict)
    facts_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    objects = EvidenceQuerySet.as_manager()

    class Meta:
        ordering = ['-created_at', '-id']
        verbose_name = verbose_name_plural = '资金核验证据'
        default_permissions = ('view',)
        permissions = [('resolve_payments', '核验及处理异常付款退款'),
                       ('export_financial_reconciliation', '导出商户资金对账清单')]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('资金核验证据不可修改。')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('资金核验证据不可删除。')
