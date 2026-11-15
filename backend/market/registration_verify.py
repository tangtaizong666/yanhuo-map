"""Graphic captcha and mobile verification challenge for account registration."""
import re
import secrets
import urllib.parse
from django.conf import settings
from django.core.cache import cache
from django.utils.crypto import constant_time_compare, salted_hmac
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from .auth_limits import client_ip_bucket
from .errors import BusinessError
from .recovery_models import VerifiedAccountContact

CAPTCHA_ALPHABET = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ'
CAPTCHA_TTL_SECONDS = 300
SMS_CODE_TTL_SECONDS = 300
SMS_COOLDOWN_SECONDS = 60
SMS_MAX_ATTEMPTS = 5


class CaptchaThrottle(SimpleRateThrottle):
    scope = 'captcha'

    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': client_ip_bucket(request)}


def normalize_phone(value) -> str:
    if not isinstance(value, str):
        raise BusinessError('请填写有效的 11 位中国大陆手机号。', 'invalid_phone', status=400)
    cleaned = re.sub(r'[\s-]', '', value)
    if cleaned.startswith('+86'):
        cleaned = cleaned[3:]
    if not re.fullmatch(r'1[3-9]\d{9}', cleaned):
        raise BusinessError('请填写有效的 11 位中国大陆手机号。', 'invalid_phone', status=400)
    return cleaned


def phone_digest(phone: str) -> str:
    return salted_hmac('yanhuo-verified-phone', phone, algorithm='sha256').hexdigest()


def mask_phone(phone: str) -> str:
    return f'{phone[:3]}****{phone[-4:]}'


def _captcha_digest(captcha_id: str, code: str) -> str:
    return salted_hmac('yanhuo-reg-captcha', f'{captcha_id}:{code.upper()}', algorithm='sha256').hexdigest()


def _sms_digest(p_hash: str, code: str) -> str:
    return salted_hmac('yanhuo-reg-sms', f'{p_hash}:{code}', algorithm='sha256').hexdigest()


def _no_store_response(data: dict, status: int = 200) -> Response:
    response = Response(data, status=status)
    response['Cache-Control'] = 'no-store, private'
    response['Pragma'] = 'no-cache'
    return response


def _build_captcha_svg(code: str) -> str:
    palette = ('#9c471a', '#6d3e1e', '#b35522', '#4c5f3f')
    glyphs = []
    for idx, ch in enumerate(code):
        x = 20 + idx * 27 + secrets.randbelow(5)
        y = 30 + secrets.randbelow(5) - 2
        angle = secrets.randbelow(25) - 12
        color = palette[(idx + secrets.randbelow(len(palette))) % len(palette)]
        glyphs.append(
            f'<text x="{x}" y="{y}" fill="{color}" font-family="Georgia, monospace" '
            f'font-size="23" font-weight="700" transform="rotate({angle} {x} {y})">{ch}</text>'
        )
    y1, y2 = 12 + secrets.randbelow(20), 14 + secrets.randbelow(18)
    y3, y4 = 10 + secrets.randbelow(24), 12 + secrets.randbelow(20)
    dots = ''.join(
        f'<circle cx="{8 + secrets.randbelow(116)}" cy="{6 + secrets.randbelow(32)}" r="1.3" fill="#d9bea0"/>'
        for _ in range(10)
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="132" height="44" viewBox="0 0 132 44">'
        '<rect width="132" height="44" rx="10" fill="#fff6ea" stroke="#e6d3bc"/>'
        f'<path d="M8 {y1} Q 66 {y2}, 124 {y3}" fill="none" stroke="#dfb995" stroke-width="1.5"/>'
        f'<path d="M10 {y4} Q 70 {y1}, 122 {y2}" fill="none" stroke="#d5c6b0" stroke-width="1.2"/>'
        f'{dots}{"".join(glyphs)}</svg>'
    )


def issue_captcha() -> tuple[str, str, str]:
    captcha_id = secrets.token_urlsafe(18)
    code = ''.join(secrets.choice(CAPTCHA_ALPHABET) for _ in range(4))
    cache.set(f'reg_captcha:{captcha_id}', _captcha_digest(captcha_id, code), timeout=CAPTCHA_TTL_SECONDS)
    svg = _build_captcha_svg(code)
    return captcha_id, code, f'data:image/svg+xml;utf8,{urllib.parse.quote(svg)}'


def consume_captcha(captcha_id, captcha_code) -> None:
    if not isinstance(captcha_id, str) or not isinstance(captcha_code, str):
        raise BusinessError('请先填写图形验证码。', 'invalid_captcha', status=400)
    cid = captcha_id.strip()
    ccode = captcha_code.strip().upper()
    if not cid or not re.fullmatch(r'[A-Z0-9]{4}', ccode):
        if cid:
            cache.delete(f'reg_captcha:{cid}')
        raise BusinessError('图形验证码不正确或已过期，请点击图片刷新后重试。', 'invalid_captcha', status=400)
    key = f'reg_captcha:{cid}'
    expected = cache.get(key)
    cache.delete(key)
    if not expected or not constant_time_compare(expected, _captcha_digest(cid, ccode)):
        raise BusinessError('图形验证码不正确或已过期，请点击图片刷新后重试。', 'invalid_captcha', status=400)


def ensure_phone_available(p_hash: str) -> None:
    existing = VerifiedAccountContact.objects.select_related('user').filter(phone_hash=p_hash).first()
    if existing and existing.user.is_active:
        raise BusinessError('该手机号已绑定已有账号，请直接登录或使用恢复码找回。', 'phone_taken', status=400)
    if existing and not existing.user.is_active:
        existing.delete()


def verify_register_code(p_hash: str, code: str) -> None:
    key = f'reg_sms_code:{p_hash}'
    record = cache.get(key)
    if not isinstance(record, dict) or 'hash' not in record:
        raise BusinessError('手机验证码已过期或尚未获取，请先点击获取验证码。', 'invalid_verification_code', status=400)
    if not constant_time_compare(record['hash'], _sms_digest(p_hash, code)):
        attempts = int(record.get('attempts', 0)) + 1
        if attempts >= SMS_MAX_ATTEMPTS:
            cache.delete(key)
            raise BusinessError('验证码错误次数过多，请重新获取手机验证码。', 'verification_attempts_exceeded', status=400)
        cache.set(key, {'hash': record['hash'], 'attempts': attempts}, timeout=SMS_CODE_TTL_SECONDS)
        raise BusinessError('手机验证码不正确，请核对后重试。', 'invalid_verification_code', status=400)


def consume_register_code(p_hash: str) -> None:
    cache.delete(f'reg_sms_code:{p_hash}')
    cache.delete(f'reg_sms_cooldown:{p_hash}')


@api_view(['GET'])
@permission_classes([AllowAny])
@throttle_classes([CaptchaThrottle])
def captcha_view(request):
    captcha_id, code, svg_uri = issue_captcha()
    payload = {
        'captcha_id': captcha_id,
        'svg': svg_uri,
        'expires_in_seconds': CAPTCHA_TTL_SECONDS,
    }
    if settings.DEMO_MODE:
        payload['demo_captcha_code'] = code
    return _no_store_response(payload)


@api_view(['POST'])
@permission_classes([AllowAny])
def send_register_code(request):
    from .views import AuthThrottle, csrf
    csrf(request)
    throttle = AuthThrottle()
    if not throttle.allow_request(request, send_register_code):
        from rest_framework.exceptions import Throttled
        raise Throttled(wait=throttle.wait(), detail='验证码请求过于频繁，请稍后再试。')
    phone = normalize_phone(request.data.get('phone', ''))
    consume_captcha(request.data.get('captcha_id', ''), request.data.get('captcha_code', ''))
    p_hash = phone_digest(phone)
    ensure_phone_available(p_hash)
    cooldown_key = f'reg_sms_cooldown:{p_hash}'
    if cache.get(cooldown_key):
        raise BusinessError('验证码已发送，请等待 60 秒后再重新获取。', 'sms_cooldown', status=429)
    if not settings.DEMO_MODE:
        raise BusinessError('生产短信网关尚未配置，请联系平台运营开通短信验证。', 'sms_unavailable', status=503)
    sms_code = f'{secrets.randbelow(900000) + 100000}'
    cache.set(f'reg_sms_code:{p_hash}', {'hash': _sms_digest(p_hash, sms_code), 'attempts': 0}, timeout=SMS_CODE_TTL_SECONDS)
    cache.set(cooldown_key, '1', timeout=SMS_COOLDOWN_SECONDS)
    return _no_store_response({
        'sent': True,
        'masked_phone': mask_phone(phone),
        'cooldown_seconds': SMS_COOLDOWN_SECONDS,
        'expires_in_seconds': SMS_CODE_TTL_SECONDS,
        'delivery_mode': 'demo',
        'demo_code': sms_code,
    })
