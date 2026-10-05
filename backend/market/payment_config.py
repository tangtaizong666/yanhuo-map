"""Resolve each verified business's own payment account; no platform collection wallet."""
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings

from .wechatpay import ConfigurationError, WechatPayClient, _https_public_url


def _local_path(value):
    if not isinstance(value, str) or not value or value.startswith(('\\\\', '//')):
        raise ValueError('local file path required')
    return Path(value)


def _accounts():
    try:
        configured = settings.WECHAT_PAY_CONFIG_FILE
        path = _local_path(configured)
        if not path.is_absolute():
            raise ValueError('absolute config path required')
        path = path.resolve()
        if not path.is_file() or path.stat().st_size > 256 * 1024:
            raise ValueError('config too large')
        def unique_keys(pairs):
            result = {}
            for key, value in pairs:
                if key in result: raise ValueError('duplicate key')
                result[key] = value
            return result
        data = json.loads(path.read_text(encoding='utf-8-sig'), object_pairs_hook=unique_keys)
        accounts = data['accounts']
        if not isinstance(accounts, dict) or not accounts or len(accounts) > 1000:
            raise ValueError('accounts required')
        bindings, receivers = set(), set()
        for key, account in accounts.items():
            if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', key) or not isinstance(account, dict):
                raise ValueError('invalid account')
            identity, receiver = account.get('merchant_profile_id'), account.get('mchid')
            if type(identity) is not int or identity < 1 or identity in bindings or not isinstance(receiver, str) or receiver in receivers:
                raise ValueError('one independent receiving account per business')
            bindings.add(identity)
            receivers.add(receiver)
        return path.parent, accounts
    except (AttributeError, OSError, KeyError, ValueError, TypeError):
        raise ConfigurationError() from None


def _configuration(account_key):
    if not isinstance(account_key, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', account_key):
        raise ConfigurationError()
    directory, accounts = _accounts()
    account = accounts.get(account_key)
    if not account:
        raise ConfigurationError()
    origin = settings.WECHAT_PAY_PUBLIC_ORIGIN
    if not isinstance(origin, str):
        raise ConfigurationError('微信支付的 HTTPS 站点和回调地址尚未就绪。')
    origin = origin.rstrip('/')
    if not _https_public_url(origin) or urlsplit(origin).path:
        raise ConfigurationError('微信支付的 HTTPS 站点和回调地址尚未就绪。')
    config = dict(account)
    config['enabled'] = settings.WECHAT_PAY_ENABLED and not settings.DEMO_MODE and account.get('enabled') is True
    config['notify_url'] = f'{origin}/api/v1/payments/wechat/notify/{account_key}'
    config['refund_notify_url'] = config['notify_url']
    try:
        def secret_path(value, maximum):
            path = _local_path(value)
            relative = not path.is_absolute()
            path = (directory / path if relative else path).resolve()
            if relative and not path.is_relative_to(directory):
                raise ValueError('relative secret path leaves config directory')
            if not path.is_file() or not 0 < path.stat().st_size <= maximum:
                raise ValueError('invalid secret file')
            return str(path)
        config['private_key_path'] = secret_path(config['private_key_path'], 64 * 1024)
        config['payment_public_keys'] = {key: secret_path(path, 64 * 1024) for key, path in config['payment_public_keys'].items()}
        # The API v3 symmetric key is read from its own secret file, not stored in the example JSON.
        key_file = Path(secret_path(config['api_v3_key_file'], 128))
        config['api_v3_key'] = key_file.read_text(encoding='utf-8').strip()
        if len(config['api_v3_key'].encode('utf-8')) != 32:
            raise ValueError('invalid key')
    except (KeyError, OSError, TypeError, ValueError, AttributeError):
        raise ConfigurationError() from None
    return config


def get_payment_client(account_key):
    # Reconciliation must remain possible when creation is disabled. The gateway
    # applies enabled only to create_payment, while still verifying older results.
    return WechatPayClient(_configuration(account_key))


def get_merchant_payment_readiness(merchant):
    unavailable = {'available': False, 'channels': [], 'account_key': '',
        'reason': '微信支付尚未开通，请使用到摊付款。'}
    if settings.DEMO_MODE:
        return {**unavailable, 'reason': '示例环境不发起真实微信扣款，当前支持到摊付款。'}
    if not settings.WECHAT_PAY_ENABLED or not merchant.is_verified:
        return unavailable
    # Qualification covers all online transactions, including pay-at-stall reservations.
    trade_reason = merchant.online_trade_reason()
    if trade_reason:
        return {**unavailable, 'reason': trade_reason}
    account_key = getattr(merchant, 'wechat_pay_account', None)
    try:
        config = _configuration(account_key)
        if config.get('merchant_profile_id') != merchant.pk or not config['enabled']:
            return unavailable
        client = WechatPayClient(config)
    except ConfigurationError:
        return unavailable
    return {'available': True, 'channels': list(client.channels),
        'account_key': account_key, 'reason': ''}
