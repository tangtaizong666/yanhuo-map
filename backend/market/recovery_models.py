from django.conf import settings
from django.db import models


class AccountRecovery(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='account_recovery')
    code_hash = models.CharField(max_length=64, blank=True)
    password_stamp = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now=True)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = verbose_name_plural = '账号恢复码状态'
