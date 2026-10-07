"""Persistent authentication protection and catalogue request identities."""
from django.db import models
from django.utils import timezone


class AuthenticationFailureBucket(models.Model):
    key = models.CharField(max_length=64, primary_key=True)
    failures = models.PositiveIntegerField(default=0)
    window_started_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now, db_index=True)


class ProductCreation(models.Model):
    stall = models.ForeignKey('market.Stall', on_delete=models.CASCADE)
    idempotency_key = models.CharField(max_length=128)
    request_hash = models.CharField(max_length=64)
    product = models.ForeignKey('market.Product', on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['stall', 'idempotency_key'], name='unique_product_creation_key')]


class MerchantOrderOperation(models.Model):
    order = models.ForeignKey('market.Order', on_delete=models.CASCADE, related_name='merchant_operations')
    idempotency_key = models.CharField(max_length=128)
    action = models.CharField(max_length=32)
    request_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['order', 'idempotency_key'], name='unique_merchant_order_operation')]
