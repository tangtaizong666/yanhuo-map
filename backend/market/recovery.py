"""One-time, opt-in account recovery. Plain recovery codes are never persisted."""
import hashlib
import re
import secrets
from django.contrib.auth import get_user_model, logout
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.views.decorators.debug import sensitive_post_parameters
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from .errors import BusinessError
from .recovery_models import AccountRecovery
from .services import audit
from .views import AuthThrottle, check_password, csrf


def stamp(user):
    return hashlib.sha256(user.password.encode()).hexdigest()


def digest(user_id, code):
    return salted_hmac('yanhuo-account-recovery', f'{user_id}:{code}', algorithm='sha256').hexdigest()


def available(record, user):
    return bool(record and record.code_hash and not record.used_at and
                constant_time_compare(record.password_stamp, stamp(user)))


def private_response(data, status=200):
    response = Response(data, status=status)
    response['Cache-Control'] = 'no-store, private'
    response['Pragma'] = 'no-cache'
    return response


@sensitive_post_parameters('password')
@api_view(['GET', 'POST', 'DELETE'])
@permission_classes([IsAuthenticated])
@throttle_classes([AuthThrottle])
def manage_recovery(request):
    if request.method == 'GET':
        record = AccountRecovery.objects.filter(user=request.user).first()
        return private_response({'enabled': available(record, request.user),
                                 'created_at': record.created_at if record else None})
    password = serializers.CharField(max_length=128, trim_whitespace=False).run_validation(request.data.get('password'))
    with transaction.atomic():
        user = get_user_model().objects.select_for_update().get(pk=request.user.pk)
        if not user.is_active or not user.check_password(password):
            raise BusinessError('当前密码不正确。', 'invalid_password', status=400)
        if request.method == 'DELETE':
            AccountRecovery.objects.filter(user=user).update(code_hash='', password_stamp='', used_at=timezone.now())
            audit(user, 'account_recovery_revoked', user.pk)
            return private_response({'enabled': False})
        raw = secrets.token_hex(16).upper()
        AccountRecovery.objects.update_or_create(user=user, defaults={
            'code_hash': digest(user.pk, raw), 'password_stamp': stamp(user), 'used_at': None})
        audit(user, 'account_recovery_created', user.pk)
        return private_response({'enabled': True, 'recovery_code': '-'.join(raw[i:i+4] for i in range(0, 32, 4)),
                                 'detail': '仅本次显示，请保存到安全位置。重新生成会使旧码失效。'})


@sensitive_post_parameters('recovery_code', 'new_password')
@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([AuthThrottle])
def reset_password(request):
    csrf(request)
    username = serializers.CharField(max_length=150).run_validation(request.data.get('username'))
    code = serializers.CharField(max_length=80).run_validation(request.data.get('recovery_code'))
    password = serializers.CharField(max_length=128, trim_whitespace=False).run_validation(request.data.get('new_password'))
    normalized = re.sub(r'[\s-]', '', code).upper()
    failure = BusinessError('账号或恢复码不正确，或恢复码已经失效。', 'invalid_recovery', status=400)
    if not re.fullmatch(r'[0-9A-F]{32}', normalized):
        raise failure
    with transaction.atomic():
        user = get_user_model().objects.select_for_update().filter(username=username, is_active=True).first()
        record = AccountRecovery.objects.filter(user=user).first() if user else None
        candidate = digest(user.pk if user else 0, normalized)
        if not (user and available(record, user) and constant_time_compare(record.code_hash, candidate)):
            raise failure
        check_password(password, user)
        # Changing to the same password would not invalidate other sessions.
        if user.check_password(password):
            raise BusinessError('新密码不能与原密码相同。', 'unchanged_password', status=400)
        user.set_password(password)
        user.save(update_fields=['password'])
        record.code_hash = ''
        record.password_stamp = ''
        record.used_at = timezone.now()
        record.save(update_fields=['code_hash', 'password_stamp', 'used_at'])
        audit(user, 'account_password_recovered', user.pk)
        from .auth_limits import clear_account_failures
        transaction.on_commit(lambda: clear_account_failures(user.username))
    logout(request)
    return private_response({'detail': '密码已重设。请使用新密码登录，其他设备需要重新登录。恢复码已用完。'})
