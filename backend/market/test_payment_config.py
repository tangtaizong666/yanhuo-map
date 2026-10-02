"""Server-owned account isolation; all files and gateway objects are test-only."""
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from .payment_config import get_merchant_payment_readiness, get_payment_client
from .wechatpay import ConfigurationError


class PaymentConfigurationTests(SimpleTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='yanhuo-pay-config-')
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve()
        self.config_path = self.directory / 'accounts.json'
        for name in ('shop-one-private.pem', 'shop-two-private.pem', 'wechat-public.pem'):
            (self.directory / name).write_text('isolated-test-placeholder', encoding='utf-8')
        (self.directory / 'shop-one-key.txt').write_text('a' * 32 + '\n', encoding='utf-8')
        (self.directory / 'shop-two-key.txt').write_text('b' * 32, encoding='utf-8')
        self.accounts = {
            'shop_one': self.account(1, '1900000101', 'one'),
            'shop_two': self.account(2, '1900000102', 'two'),
        }
        self.save()
        self.settings = override_settings(DEMO_MODE=False, WECHAT_PAY_ENABLED=True,
            WECHAT_PAY_CONFIG_FILE=str(self.config_path), WECHAT_PAY_PUBLIC_ORIGIN='https://pay.example.com')
        self.settings.enable()
        self.addCleanup(self.settings.disable)

        def client(config):
            return SimpleNamespace(config=config, mchid=config['mchid'], appid=config['appid'],
                channels=tuple(config.get('channels', ['native'])), enabled=config['enabled'],
                query_payment=Mock(return_value={'trade_state': 'NOTPAY'}))
        gateway_patch = patch('market.payment_config.WechatPayClient', side_effect=client)
        self.gateway = gateway_patch.start()
        self.addCleanup(gateway_patch.stop)
        self.first = SimpleNamespace(pk=1, is_verified=True, wechat_pay_account='shop_one')
        self.second = SimpleNamespace(pk=2, is_verified=True, wechat_pay_account='shop_two')

    def account(self, identity, mchid, name):
        return {'merchant_profile_id': identity, 'enabled': True, 'mode': 'direct',
            'appid': f'wx{name}', 'mchid': mchid, 'serial_no': 'ABC123',
            'private_key_path': f'shop-{name}-private.pem', 'api_v3_key_file': f'shop-{name}-key.txt',
            'payment_public_keys': {'PUB_KEY_ID_123456': 'wechat-public.pem'}, 'channels': ['native']}

    def save(self):
        self.config_path.write_text(json.dumps({'accounts': self.accounts}), encoding='utf-8')

    def test_demo_global_disabled_and_unverified_merchant_never_open_channel(self):
        for settings in ({'DEMO_MODE': True}, {'WECHAT_PAY_ENABLED': False}):
            with self.subTest(settings=settings), override_settings(**settings):
                result = get_merchant_payment_readiness(self.first)
                self.assertFalse(result['available'])
                self.assertEqual(result['channels'], [])
                self.assertEqual(result['account_key'], '')
        self.first.is_verified = False
        self.assertFalse(get_merchant_payment_readiness(self.first)['available'])
        self.gateway.assert_not_called()

    def test_merchant_binding_must_match_and_cannot_borrow_another_account(self):
        self.first.wechat_pay_account = 'shop_two'
        self.assertFalse(get_merchant_payment_readiness(self.first)['available'])
        self.gateway.assert_not_called()
        self.first.wechat_pay_account = 'missing'
        self.assertFalse(get_merchant_payment_readiness(self.first)['available'])
        self.gateway.assert_not_called()

    def test_independent_businesses_receive_separate_clients_and_callback_paths(self):
        first = get_merchant_payment_readiness(self.first)
        second = get_merchant_payment_readiness(self.second)
        self.assertTrue(first['available'])
        self.assertTrue(second['available'])
        first_config, second_config = [call.args[0] for call in self.gateway.call_args_list]
        self.assertEqual((first_config['mchid'], second_config['mchid']), ('1900000101', '1900000102'))
        self.assertEqual((first_config['api_v3_key'], second_config['api_v3_key']), ('a' * 32, 'b' * 32))
        self.assertEqual(first_config['notify_url'], 'https://pay.example.com/api/v1/payments/wechat/notify/shop_one')
        self.assertEqual(second_config['notify_url'], 'https://pay.example.com/api/v1/payments/wechat/notify/shop_two')
        self.assertEqual(first_config['refund_notify_url'], first_config['notify_url'])
        self.assertEqual(first_config['private_key_path'], str(self.directory / 'shop-one-private.pem'))
        self.assertNotIn('api_v3_key', first)
        self.assertNotIn('mchid', first)
        self.assertNotIn('private_key_path', first)

    def test_duplicate_merchant_or_receiving_account_rejects_configuration(self):
        for changed in ({'merchant_profile_id': 1}, {'mchid': '1900000101'}):
            with self.subTest(fields=list(changed)):
                self.accounts['shop_two'] = {**self.account(2, '1900000102', 'two'), **changed}
                self.save()
                self.assertFalse(get_merchant_payment_readiness(self.first)['available'])
                with self.assertRaises(ConfigurationError):
                    get_payment_client('shop_one')
        self.gateway.assert_not_called()

    def test_invalid_account_ids_and_duplicate_json_keys_reject_configuration(self):
        for identity in (True, 0, '1', None):
            self.accounts['shop_one']['merchant_profile_id'] = identity
            self.save()
            self.assertFalse(get_merchant_payment_readiness(self.first)['available'])
        self.config_path.write_text('{"accounts":{},"accounts":{}}', encoding='utf-8')
        self.assertFalse(get_merchant_payment_readiness(self.first)['available'])
        self.gateway.assert_not_called()

    def test_disabled_creation_still_allows_building_historical_query_client(self):
        self.accounts['shop_one']['enabled'] = False
        self.save()
        for settings in ({}, {'WECHAT_PAY_ENABLED': False}, {'DEMO_MODE': True}):
            with self.subTest(settings=settings), override_settings(**settings):
                self.assertFalse(get_merchant_payment_readiness(self.first)['available'])
                client = get_payment_client('shop_one')
                self.assertFalse(client.enabled)
                self.assertEqual(client.query_payment('stable-old-order'), {'trade_state': 'NOTPAY'})

    def test_callback_origin_is_https_public_origin_without_path_or_credentials(self):
        for origin in (None, '', 'http://pay.example.com', 'https://localhost', 'https://127.0.0.1',
                       'https://10.53.8.40', 'https://pay.example.com/path', 'https://pay.example.com/?x=1',
                       'https://pay.example.com/#fragment', 'https://user:pass@pay.example.com',
                       'https://pay.example.com:5183', 'https://pay.example.com:invalid'):
            with self.subTest(origin=origin), override_settings(WECHAT_PAY_PUBLIC_ORIGIN=origin):
                self.assertFalse(get_merchant_payment_readiness(self.first)['available'])
        self.gateway.assert_not_called()

    def test_callback_urls_are_generated_and_cannot_be_overridden_by_account_json(self):
        self.accounts['shop_one']['notify_url'] = 'https://unrelated.example/collect'
        self.accounts['shop_one']['refund_notify_url'] = 'https://unrelated.example/refund'
        self.save()
        client = get_payment_client('shop_one')
        self.assertEqual(client.config['notify_url'], 'https://pay.example.com/api/v1/payments/wechat/notify/shop_one')
        self.assertEqual(client.config['refund_notify_url'], client.config['notify_url'])

    def test_missing_broken_and_relative_config_path_become_unavailable_without_secret_details(self):
        for path in ('', 'accounts.json', str(self.directory / 'nonexistent-secret-accounts.json'),
                     r'\\untrusted-server\share\accounts.json'):
            with self.subTest(path=path), override_settings(WECHAT_PAY_CONFIG_FILE=path):
                result = get_merchant_payment_readiness(self.first)
                self.assertFalse(result['available'])
                if path:
                    self.assertNotIn(path, result['reason'])
        self.config_path.write_text('not-json secret-never-expose', encoding='utf-8')
        result = get_merchant_payment_readiness(self.first)
        self.assertFalse(result['available'])
        self.assertNotIn('secret-never-expose', result['reason'])
        self.gateway.assert_not_called()

    def test_secret_files_are_local_bounded_and_relative_paths_cannot_escape(self):
        for field, value in (('private_key_path', r'\\untrusted-server\share\private.pem'),
                             ('private_key_path', '../private.pem'), ('private_key_path', 'missing.pem'),
                             ('api_v3_key_file', r'\\untrusted-server\share\api-key.txt'),
                             ('api_v3_key_file', '../api-key.txt')):
            with self.subTest(field=field, value=value):
                self.accounts['shop_one'] = {**self.account(1, '1900000101', 'one'), field: value}
                self.save()
                self.assertFalse(get_merchant_payment_readiness(self.first)['available'])
        self.accounts['shop_one'] = self.account(1, '1900000101', 'one')
        self.save()
        (self.directory / 'shop-one-private.pem').write_bytes(b'X' * (64 * 1024 + 1))
        self.assertFalse(get_merchant_payment_readiness(self.first)['available'])
        self.gateway.assert_not_called()

    def test_invalid_api_key_file_and_invalid_pem_are_not_reported_ready(self):
        for value in ('short', 'a' * 129, ''):
            (self.directory / 'shop-one-key.txt').write_text(value, encoding='utf-8')
            self.assertFalse(get_merchant_payment_readiness(self.first)['available'])
        self.gateway.assert_not_called()
        (self.directory / 'shop-one-key.txt').write_text('a' * 32, encoding='utf-8')
        self.gateway.side_effect = ConfigurationError()
        self.assertFalse(get_merchant_payment_readiness(self.first)['available'])

    def test_explicit_absolute_local_secrets_are_allowed_outside_config_directory(self):
        nested = self.directory / 'deployment'
        nested.mkdir()
        config_file = nested / 'accounts.json'
        account = self.accounts['shop_one']
        account['private_key_path'] = str(self.directory / 'shop-one-private.pem')
        account['api_v3_key_file'] = str(self.directory / 'shop-one-key.txt')
        account['payment_public_keys'] = {'PUB_KEY_ID_123456': str(self.directory / 'wechat-public.pem')}
        config_file.write_text(json.dumps({'accounts': {'shop_one': account}}), encoding='utf-8')
        with override_settings(WECHAT_PAY_CONFIG_FILE=str(config_file)):
            self.assertTrue(get_merchant_payment_readiness(self.first)['available'])
