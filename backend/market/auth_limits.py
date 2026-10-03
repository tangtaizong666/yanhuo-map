"""Trusted peer identity and database-atomic failed-login limits for both UIs."""
import ipaddress
import logging
import math
from datetime import timedelta

from django.conf import settings
from django.contrib.admin.forms import AdminAuthenticationForm
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle
from .security_models import AuthenticationFailureBucket

logger = logging.getLogger('market')


def client_ip(request, *, trust_proxy=None):
    trusted = settings.AUTH_TRUST_PROXY_CLIENT_IP if trust_proxy is None else trust_proxy
    if trusted:
        try:
            return str(ipaddress.ip_address(request.META.get('HTTP_X_REAL_IP', '')))
        except ValueError:
            pass
    return request.META.get('REMOTE_ADDR', '')


class TrustedAnonRateThrottle(AnonRateThrottle):
    def get_ident(self, request):
        return client_ip(request)


class TrustedUserRateThrottle(UserRateThrottle):
    def get_ident(self, request):
        return client_ip(request)


class LoginRateLimited(Exception):
    def __init__(self, retry_after):
        self.retry_after = retry_after


def _key(scope, identity):
    # Neither usernames nor peer addresses are retained in these counters/logs.
    return salted_hmac('login-failures', f'{scope}:{identity}', algorithm='sha256').hexdigest()


def login_attempt(request, username, authenticate_callback):
    """Serialize checks and failures across workers; successful attempts cost zero.

    callback returns a user/cleaned form or raises ValidationError. A validation
    failure is re-raised after committing counters, never inside the transaction.
    """
    window = timedelta(seconds=settings.AUTH_FAILURE_WINDOW_SECONDS)
    candidates = sorted([
        (_key('account', username.casefold()), settings.AUTH_FAILURE_ACCOUNT_LIMIT),
        (_key('ip', client_ip(request)), settings.AUTH_FAILURE_IP_LIMIT),
    ])
    validation_error = None
    with transaction.atomic():
        rows = []
        now = timezone.now()
        # Consistent lock order also covers first-use races through unique keys.
        for key, limit in candidates:
            AuthenticationFailureBucket.objects.get_or_create(key=key)
            row = AuthenticationFailureBucket.objects.select_for_update().get(pk=key)
            if row.window_started_at + window <= now:
                row.failures, row.window_started_at = 0, now
            if row.failures >= limit:
                wait = max(1, math.ceil((row.window_started_at + window - now).total_seconds()))
                logger.warning('login_rate_limited bucket=%s', key[:12])
                raise LoginRateLimited(wait)
            rows.append(row)
        try:
            result = authenticate_callback()
        except ValidationError as exc:
            result, validation_error = None, exc
        if result is None:
            for row in rows:
                row.failures += 1
                row.updated_at = now
                row.save(update_fields=['failures', 'window_started_at', 'updated_at'])
            logger.info('login_failed account_bucket=%s', _key('account', username.casefold())[:12])
    if validation_error is not None:
        raise validation_error
    return result


class LimitedAdminAuthenticationForm(AdminAuthenticationForm):
    def clean(self):
        username = self.cleaned_data.get('username')
        password = self.cleaned_data.get('password')
        if not username or not password:
            return super().clean()
        try:
            return login_attempt(self.request, username, super().clean)
        except LoginRateLimited as exc:
            self.request._login_retry_after = exc.retry_after
            raise ValidationError('登录失败次数过多，请稍后再试。', code='login_rate_limited')


class AdminLoginLimitStatusMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        wait = getattr(request, '_login_retry_after', None)
        if wait:
            response.status_code = 429
            response['Retry-After'] = str(wait)
            response['Cache-Control'] = 'no-store, private'
        return response
