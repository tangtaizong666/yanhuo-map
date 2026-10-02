"""Small, fail-closed WeChat Pay API v3 adapter for one direct merchant account.

This module never chooses a receiving merchant, changes orders, retries a payment,
or treats a browser redirect as payment evidence. The caller must bind an account
to its verified business, persist stable trade/refund numbers, and compare every
verified result with the saved account, amount, currency and order snapshot.
"""
from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import re
import secrets
import socket
import ssl
import time
from collections.abc import Mapping
from datetime import datetime
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from cryptography.exceptions import InvalidSignature, InvalidTag, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


API_ORIGIN = 'https://api.mch.weixin.qq.com'
MAX_BODY_BYTES = 1024 * 1024
SIGNATURE_MAX_AGE_SECONDS = 300


class GatewayError(Exception):
    """Safe to expose message; no provider payload, PII, key or local path."""

    def __init__(self, code, safe_message, *, retryable=False, outcome_unknown=True,
                 status_code=None):
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable
        self.outcome_unknown = outcome_unknown
        self.status_code = status_code


class ConfigurationError(GatewayError):
    def __init__(self, message='微信支付尚未完成商户配置，请使用到摊付款。'):
        super().__init__('PAYMENT_NOT_CONFIGURED', message, outcome_unknown=False)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _invalid_argument(message):
    return GatewayError('INVALID_PAYMENT_ARGUMENT', message, outcome_unknown=False)


def _https_public_url(value):
    if not isinstance(value, str) or len(value) > 256 or any(c.isspace() for c in value):
        return False
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname or ''
        if (parsed.scheme != 'https' or not hostname or parsed.username or parsed.password
                or parsed.fragment or parsed.query or parsed.port not in (None, 443)
                or hostname == 'localhost' or hostname.endswith(('.localhost', '.local'))):
            return False
        try:
            return ipaddress.ip_address(hostname).is_global
        except ValueError:
            return '.' in hostname
    except ValueError:
        return False


def _json_object(raw):
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate key')
            result[key] = value
        return result
    def reject_constant(value):
        raise ValueError('non-JSON numeric constant')
    value = json.loads(raw, object_pairs_hook=unique_keys, parse_constant=reject_constant)
    if not isinstance(value, dict):
        raise ValueError('object required')
    return value


def _identifier(value, maximum, minimum=1):
    pattern = r'[A-Za-z0-9_\-|*]+' if maximum == 32 else r'[A-Za-z0-9_\-|*@]+'
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise _invalid_argument('支付单号格式错误。')
    if not minimum <= len(value) <= maximum:
        raise _invalid_argument('支付单号长度错误。')
    return value


class WechatPayClient:
    """Direct-merchant Native/H5; JSAPI and service-provider mode are not enabled.

    Config keys: enabled, appid, mchid, serial_no, private_key_path, api_v3_key,
    payment_public_keys ({WeChat public-key ID: PEM path}), notify_url,
    refund_notify_url, channels ([native, h5]), optional mode (direct), timeout.
    Disable `enabled` to stop new payment sessions while retaining reconciliation.
    A trusted operator supplies paths; never accept this config from a web request.
    """

    def __init__(self, config: Mapping):
        if not isinstance(config, Mapping) or config.get('mode', 'direct') != 'direct':
            raise ConfigurationError()
        self.enabled = config.get('enabled') is True
        self.appid = config.get('appid', '')
        self.mchid = config.get('mchid', '')
        self.serial_no = config.get('serial_no', '')
        self.notify_url = config.get('notify_url', '')
        self.refund_notify_url = config.get('refund_notify_url', '')
        channels = config.get('channels', ['native'])
        if (not isinstance(self.appid, str) or not re.fullmatch(r'[A-Za-z0-9]{1,32}', self.appid)
                or not isinstance(self.mchid, str) or not re.fullmatch(r'\d{6,32}', self.mchid)
                or not isinstance(self.serial_no, str) or not re.fullmatch(r'[A-Fa-f0-9]{1,64}', self.serial_no)
                or not _https_public_url(self.notify_url)
                or not _https_public_url(self.refund_notify_url)
                or not isinstance(channels, (list, tuple)) or not channels
                or any(channel not in ('native', 'h5') for channel in channels)):
            raise ConfigurationError()
        self.channels = tuple(dict.fromkeys(channels))
        api_key = config.get('api_v3_key', '')
        if not isinstance(api_key, str) or len(api_key.encode('utf-8')) != 32:
            raise ConfigurationError()
        self._api_v3_key = api_key.encode('utf-8')
        keys = config.get('payment_public_keys')
        if not isinstance(keys, Mapping) or not keys or len(keys) > 10:
            raise ConfigurationError()
        try:
            self._private_key = serialization.load_pem_private_key(
                Path(config['private_key_path']).read_bytes(), password=None)
            if not isinstance(self._private_key, rsa.RSAPrivateKey) or self._private_key.key_size < 2048:
                raise ValueError('RSA key required')
            self._public_keys = {}
            for key_id, path in keys.items():
                if not isinstance(key_id, str) or not re.fullmatch(r'PUB_KEY_ID_\d+', key_id):
                    raise ValueError('public key ID required')
                key = serialization.load_pem_public_key(Path(path).read_bytes())
                if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 2048:
                    raise ValueError('RSA key required')
                self._public_keys[key_id] = key
            self.timeout = float(config.get('timeout', 8))
            if not 1 <= self.timeout <= 15:
                raise ValueError('invalid timeout')
        except (KeyError, OSError, ValueError, TypeError, UnsupportedAlgorithm):
            raise ConfigurationError() from None
        # Pinned WeChat key IDs allow overlapping keys during an operator-led rotation.
        self._preferred_public_key_id = next(iter(self._public_keys))
        self._opener = build_opener(_NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))

    def _authorization(self, method, target, body):
        timestamp = str(int(time.time()))
        nonce = secrets.token_hex(16)
        message = f'{method}\n{target}\n{timestamp}\n{nonce}\n'.encode() + body + b'\n'
        signature = base64.b64encode(self._private_key.sign(
            message, padding.PKCS1v15(), hashes.SHA256())).decode('ascii')
        return ('WECHATPAY2-SHA256-RSA2048 '
                f'mchid="{self.mchid}",nonce_str="{nonce}",timestamp="{timestamp}",'
                f'serial_no="{self.serial_no}",signature="{signature}"')

    def _verify_signature(self, headers, body):
        failure = GatewayError('INVALID_PAYMENT_SIGNATURE', '支付结果尚未验证，请稍后重新查询。', retryable=True)
        try:
            normalized = {str(key).lower(): str(value) for key, value in headers.items()}
            timestamp = normalized['wechatpay-timestamp']
            nonce = normalized['wechatpay-nonce']
            signature = normalized['wechatpay-signature']
            public_key = self._public_keys[normalized['wechatpay-serial']]
            if (not re.fullmatch(r'\d{1,12}', timestamp)
                    or abs(time.time() - int(timestamp)) > SIGNATURE_MAX_AGE_SECONDS
                    or not 1 <= len(nonce) <= 128 or '\n' in nonce or '\r' in nonce
                    or signature.startswith('WECHATPAY/SIGNTEST/')):
                raise ValueError('invalid signature headers')
            signed = f'{timestamp}\n{nonce}\n'.encode('utf-8') + body + b'\n'
            public_key.verify(base64.b64decode(signature, validate=True), signed,
                              padding.PKCS1v15(), hashes.SHA256())
        except (KeyError, ValueError, TypeError, AttributeError, InvalidSignature, binascii.Error):
            raise failure from None

    def _request(self, method, target, payload=None):
        # The origin is intentionally not configurable. No redirect may carry a signed request elsewhere.
        body = b'' if payload is None else json.dumps(
            payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        request = Request(API_ORIGIN + target, data=body if method != 'GET' else None, method=method,
                          headers={'Authorization': self._authorization(method, target, body),
                                   'Accept': 'application/json', 'Content-Type': 'application/json',
                                   'Wechatpay-Serial': self._preferred_public_key_id,
                                   'User-Agent': 'YanhuoMap-WechatPay/1.0'})
        try:
            try:
                response = self._opener.open(request, timeout=self.timeout)
            except HTTPError as error:
                response = error
            with response:
                status = response.status
                response_headers = response.headers
                raw = response.read(MAX_BODY_BYTES + 1)
        except (URLError, OSError, HTTPException, socket.timeout, TimeoutError):
            raise GatewayError('PAYMENT_NETWORK_UNKNOWN', '支付通道暂未回应，结果待确认，请稍后查询。',
                               retryable=True) from None
        if len(raw) > MAX_BODY_BYTES:
            raise GatewayError('INVALID_PAYMENT_RESPONSE', '支付结果暂时无法确认，请稍后查询。', retryable=True)
        # 204 carries an empty signed body. Missing/invalid signatures never establish a terminal state.
        self._verify_signature(response_headers, raw)
        try:
            data = {} if not raw else _json_object(raw)
        except (ValueError, UnicodeError):
            raise GatewayError('INVALID_PAYMENT_RESPONSE', '支付结果暂时无法确认，请稍后查询。', retryable=True) from None
        if not 200 <= status < 300:
            provider_code = data.get('code', '')
            code = provider_code if isinstance(provider_code, str) and re.fullmatch(r'[A-Z0-9_]{1,64}', provider_code) else 'PAYMENT_PROVIDER_ERROR'
            raise GatewayError(code, '支付通道未完成本次请求，请查询当前支付状态。',
                               retryable=status >= 500 or status == 429, status_code=status)
        return data

    def create_payment(self, *, out_trade_no, amount_cents, description, expires_at,
                       channel, payer_client_ip=None):
        if not self.enabled:
            raise ConfigurationError('微信支付当前未开放，请使用到摊付款。')
        if channel not in self.channels:
            raise GatewayError('PAYMENT_CHANNEL_UNAVAILABLE', '当前浏览器的微信支付方式尚未开放。', outcome_unknown=False)
        _identifier(out_trade_no, 32, 6)
        if type(amount_cents) is not int or not 0 < amount_cents <= 100000000:
            raise _invalid_argument('支付金额必须是有效的整数分。')
        if not isinstance(description, str) or not 1 <= len(description) <= 127:
            raise _invalid_argument('支付商品描述无效。')
        if (not isinstance(expires_at, datetime) or expires_at.tzinfo is None
                or expires_at.timestamp() < time.time() + 60):
            raise _invalid_argument('支付有效期不足一分钟，请重新获取支付信息。')
        payload = {'appid': self.appid, 'mchid': self.mchid, 'description': description,
                   'out_trade_no': out_trade_no, 'time_expire': expires_at.isoformat(timespec='seconds'),
                   'notify_url': self.notify_url, 'amount': {'total': amount_cents, 'currency': 'CNY'}}
        if channel == 'h5':
            try:
                payer_ip = str(ipaddress.ip_address(payer_client_ip))
            except ValueError:
                raise _invalid_argument('无法确认支付设备的网络地址，请稍后重试。') from None
            payload['scene_info'] = {'payer_client_ip': payer_ip, 'h5_info': {'type': 'Wap'}}
        result = self._request('POST', f'/v3/pay/transactions/{channel}', payload)
        result_url = result.get('code_url' if channel == 'native' else 'h5_url')
        if not isinstance(result_url, str) or len(result_url) > 4096:
            raise GatewayError('INVALID_PAYMENT_RESPONSE', '支付入口暂时无法确认，请重新查询。', retryable=True)
        try:
            url = urlsplit(result_url)
            valid = (url.scheme == 'weixin' and url.netloc == 'wxpay') if channel == 'native' else (
                url.scheme == 'https' and url.hostname == 'wx.tenpay.com' and url.port in (None, 443)
                and not url.username and not url.password and not url.fragment)
        except ValueError:
            valid = False
        if not valid:
            raise GatewayError('INVALID_PAYMENT_RESPONSE', '支付入口暂时无法确认，请重新查询。', retryable=True)
        return result

    def query_payment(self, out_trade_no):
        _identifier(out_trade_no, 32, 6)
        return self._request('GET', f'/v3/pay/transactions/out-trade-no/{quote(out_trade_no, safe="")}?'
                             + urlencode({'mchid': self.mchid}))

    def close_payment(self, out_trade_no):
        _identifier(out_trade_no, 32, 6)
        return self._request('POST', f'/v3/pay/transactions/out-trade-no/{quote(out_trade_no, safe="")}/close',
                             {'mchid': self.mchid})

    def refund(self, *, out_trade_no, out_refund_no, amount_cents, reason='订单退款'):
        """Request the full order amount; acceptance alone does not establish refund success."""
        _identifier(out_trade_no, 32, 6)
        _identifier(out_refund_no, 64)
        if type(amount_cents) is not int or not 0 < amount_cents <= 100000000:
            raise _invalid_argument('退款金额必须是有效的整数分。')
        if not isinstance(reason, str) or len(reason.encode('utf-8')) > 80:
            raise _invalid_argument('退款原因过长。')
        return self._request('POST', '/v3/refund/domestic/refunds', {
            'out_trade_no': out_trade_no, 'out_refund_no': out_refund_no, 'reason': reason,
            'notify_url': self.refund_notify_url,
            'amount': {'refund': amount_cents, 'total': amount_cents, 'currency': 'CNY'}})

    def query_refund(self, out_refund_no):
        _identifier(out_refund_no, 64)
        return self._request('GET', f'/v3/refund/domestic/refunds/{quote(out_refund_no, safe="")}')

    def verify_notification(self, headers, raw_body):
        if not isinstance(raw_body, bytes) or not 0 < len(raw_body) <= MAX_BODY_BYTES:
            raise GatewayError('INVALID_PAYMENT_NOTIFICATION', '支付通知格式无效。')
        self._verify_signature(headers, raw_body)
        try:
            envelope = _json_object(raw_body)
            resource = envelope['resource']
            if (envelope.get('resource_type') != 'encrypt-resource'
                    or not isinstance(envelope.get('id'), str) or not 1 <= len(envelope['id']) <= 128
                    or not isinstance(envelope.get('event_type'), str)
                    or not isinstance(resource, dict) or resource['algorithm'] != 'AEAD_AES_256_GCM'
                    or not isinstance(resource['nonce'], str)
                    or not isinstance(resource.get('associated_data', ''), str)):
                raise ValueError('invalid notification schema')
            plaintext = AESGCM(self._api_v3_key).decrypt(
                resource['nonce'].encode('utf-8'), base64.b64decode(resource['ciphertext'], validate=True),
                resource.get('associated_data', '').encode('utf-8'))
            decrypted = _json_object(plaintext)
            if decrypted.get('mchid') != self.mchid or ('appid' in decrypted and decrypted['appid'] != self.appid):
                raise ValueError('wrong receiving account')
        except (KeyError, ValueError, TypeError, UnicodeError, InvalidTag, binascii.Error):
            raise GatewayError('INVALID_PAYMENT_NOTIFICATION', '支付通知未通过校验。') from None
        return {'id': envelope['id'], 'event_type': envelope['event_type'],
                'resource': decrypted, 'create_time': envelope.get('create_time')}
