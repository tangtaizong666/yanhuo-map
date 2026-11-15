from django.conf import settings
from django.db import models
from django.utils import timezone


class AccountRecovery(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='account_recovery')
    code_hash = models.CharField(max_length=64, blank=True)
    password_stamp = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now=True)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = verbose_name_plural = '账号恢复码状态'


class VerifiedAccountContact(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='verified_contact')
    phone_hash = models.CharField(max_length=64, unique=True)
    masked_phone = models.CharField(max_length=20)
    verified_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = verbose_name_plural = '已验证注册手机'

