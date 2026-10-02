"""Isolated protocol tests: generated keys and mocked transport, never real payments."""
import base64
import io
import json
import re
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .wechatpay import API_ORIGIN, ConfigurationError, GatewayError, WechatPayClient, _NoRedirect


class FakeResponse:
    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, headers, body

    def read(self, maximum):
        return self.body[:maximum]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class WechatPayProtocolTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.directory = tempfile.TemporaryDirectory(prefix='yanhuo-wxpay-test-')
        cls.merchant_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.provider_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.private_path = Path(cls.directory.name) / 'merchant.pem'
        cls.public_path = Path(cls.directory.name) / 'wechat.pem'
        cls.private_path.write_bytes(cls.merchant_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        cls.public_path.write_bytes(cls.provider_key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()
        super().tearDownClass()

    def setUp(self):
        self.config = {
            'enabled': True, 'appid': 'wxunittest', 'mchid': '1900000109', 'serial_no': 'ABC123',
            'private_key_path': str(self.private_path), 'api_v3_key': 'a' * 32,
            'payment_public_keys': {'PUB_KEY_ID_123456': str(self.public_path)},
            'notify_url': 'https://payments.example.com/api/v1/wechat/notify/test',
            'refund_notify_url': 'https://payments.example.com/api/v1/wechat/refunds/test',
            'channels': ['native', 'h5'],
        }
        # Mock the transport factory for the entire test; accidental real requests fail immediately.
        self.transport = Mock()
        self.transport.open.side_effect = AssertionError('Real payment network access is forbidden in tests')
        self.opener_patch = patch('market.wechatpay.build_opener', return_value=self.transport)
        self.opener_patch.start()
        self.addCleanup(self.opener_patch.stop)
        self.client = WechatPayClient(self.config)

    def signed_headers(self, raw, timestamp=None):
        timestamp = str(int(time.time()) if timestamp is None else timestamp)
        nonce = 'unit-test-provider-nonce'
        signature = self.provider_key.sign(timestamp.encode() + b'\n' + nonce.encode() + b'\n' + raw + b'\n',
                                           padding.PKCS1v15(), hashes.SHA256())
        return {'Wechatpay-Timestamp': timestamp, 'Wechatpay-Nonce': nonce,
                'Wechatpay-Serial': 'PUB_KEY_ID_123456',
                'Wechatpay-Signature': base64.b64encode(signature).decode()}

    def response(self, data=None, status=200, headers=None, raw=None):
        body = raw if raw is not None else json.dumps(data or {}, ensure_ascii=False).encode()
        self.transport.open.side_effect = None
        self.transport.open.return_value = FakeResponse(status, headers or self.signed_headers(body), body)

    def create(self, **kwargs):
        arguments = {'out_trade_no': 'ORDER123456', 'amount_cents': 800, 'description': '测试餐点',
                     'expires_at': datetime.now(timezone.utc) + timedelta(minutes=5), 'channel': 'native'}
        arguments.update(kwargs)
        return self.client.create_payment(**arguments)

    def test_native_uses_exact_signed_utf8_body_and_official_origin(self):
        self.response({'code_url': 'weixin://wxpay/bizpayurl?pr=unit-test'})
        result = self.create()
        self.assertEqual(result['code_url'], 'weixin://wxpay/bizpayurl?pr=unit-test')
        request = self.transport.open.call_args.args[0]
        self.assertEqual(request.full_url, API_ORIGIN + '/v3/pay/transactions/native')
        self.assertEqual(json.loads(request.data)['amount'], {'total': 800, 'currency': 'CNY'})
        self.assertEqual(json.loads(request.data)['mchid'], self.config['mchid'])
        authorization = request.get_header('Authorization')
        fields = dict(re.findall(r'(\w+)="([^"]+)"', authorization))
        message = (f'POST\n/v3/pay/transactions/native\n{fields["timestamp"]}\n{fields["nonce_str"]}\n'.encode()
                   + request.data + b'\n')
        self.merchant_key.public_key().verify(base64.b64decode(fields['signature']), message,
                                              padding.PKCS1v15(), hashes.SHA256())
        self.assertEqual(self.transport.open.call_count, 1)

    def test_h5_requires_ip_and_returns_only_verified_official_link(self):
        self.response({'h5_url': 'https://wx.tenpay.com/cgi-bin/mmpayweb-bin/checkmweb?prepay_id=test'})
        self.create(channel='h5', payer_client_ip='203.0.113.7')
        request = self.transport.open.call_args.args[0]
        self.assertEqual(json.loads(request.data)['scene_info'], {
            'payer_client_ip': '203.0.113.7', 'h5_info': {'type': 'Wap'}})
        self.transport.open.reset_mock()
        with self.assertRaises(GatewayError):
            self.create(channel='h5', payer_client_ip='garbage')
        self.transport.open.assert_not_called()

    def test_wrong_or_unknown_signer_rejected(self):
        raw = b'{"trade_state":"SUCCESS"}'
        for change in ({'Wechatpay-Serial': 'PUB_KEY_ID_999'},
                       {'Wechatpay-Signature': 'WECHATPAY/SIGNTEST/test'},
                       {'Wechatpay-Signature': base64.b64encode(b'bad').decode()}):
            with self.subTest(change=change):
                self.response(raw=raw, headers={**self.signed_headers(raw), **change})
                with self.assertRaises(GatewayError) as raised:
                    self.client.query_payment('ORDER123456')
                self.assertEqual(raised.exception.code, 'INVALID_PAYMENT_SIGNATURE')
                self.assertTrue(raised.exception.outcome_unknown)

    def test_old_future_and_missing_signature_rejected(self):
        raw = b'{}'
        for headers in ({'Content-Type': 'application/json'}, self.signed_headers(raw, int(time.time()) - 301),
                        self.signed_headers(raw, int(time.time()) + 301)):
            with self.subTest(headers=list(headers)):
                self.response(raw=raw, headers=headers)
                with self.assertRaises(GatewayError):
                    self.client.query_payment('ORDER123456')

    def test_query_signature_includes_encoded_path_and_query(self):
        self.response({'trade_state': 'NOTPAY'})
        self.assertEqual(self.client.query_payment('ORDER|123456')['trade_state'], 'NOTPAY')
        request = self.transport.open.call_args.args[0]
        self.assertEqual(request.full_url, API_ORIGIN + '/v3/pay/transactions/out-trade-no/ORDER%7C123456?mchid=1900000109')
        fields = dict(re.findall(r'(\w+)="([^"]+)"', request.get_header('Authorization')))
        message = f'GET\n{request.selector}\n{fields["timestamp"]}\n{fields["nonce_str"]}\n\n'.encode()
        self.merchant_key.public_key().verify(base64.b64decode(fields['signature']), message,
                                              padding.PKCS1v15(), hashes.SHA256())

    def test_signed_empty_204_is_required_to_close(self):
        self.response(status=204, raw=b'')
        self.assertEqual(self.client.close_payment('ORDER123456'), {})
        self.response(status=204, raw=b'', headers={'Content-Type': 'application/json'})
        with self.assertRaises(GatewayError):
            self.client.close_payment('ORDER123456')

    def test_timeout_is_unknown_and_never_retried(self):
        self.transport.open.side_effect = URLError('private diagnostic must not escape')
        with self.assertRaises(GatewayError) as raised:
            self.create()
        self.assertTrue(raised.exception.outcome_unknown)
        self.assertTrue(raised.exception.retryable)
        self.assertNotIn('private diagnostic', str(raised.exception))
        self.assertEqual(self.transport.open.call_count, 1)

    def test_signed_provider_error_exposed_as_code_but_not_untrusted_message(self):
        raw = b'{"code":"ORDERPAID","message":"private detail"}'
        self.transport.open.side_effect = HTTPError(API_ORIGIN, 400, 'error', self.signed_headers(raw), io.BytesIO(raw))
        with self.assertRaises(GatewayError) as raised:
            self.client.close_payment('ORDER123456')
        self.assertEqual(raised.exception.code, 'ORDERPAID')
        self.assertTrue(raised.exception.outcome_unknown)
        self.assertNotIn('private detail', str(raised.exception))

    def notification(self, resource=None, **overrides):
        resource = resource or {'mchid': self.config['mchid'], 'appid': self.config['appid'],
                                'out_trade_no': 'ORDER123456', 'trade_state': 'SUCCESS',
                                'transaction_id': 'provider-transaction', 'amount': {'total': 800, 'currency': 'CNY'}}
        nonce, associated_data = b'0123456789ab', b'transaction'
        ciphertext = AESGCM(self.config['api_v3_key'].encode()).encrypt(
            nonce, json.dumps(resource).encode(), associated_data)
        envelope = {'id': 'notify-unit-test', 'event_type': 'TRANSACTION.SUCCESS', 'resource_type': 'encrypt-resource',
                    'resource': {'algorithm': 'AEAD_AES_256_GCM', 'original_type': 'transaction',
                                 'nonce': nonce.decode(), 'associated_data': associated_data.decode(),
                                 'ciphertext': base64.b64encode(ciphertext).decode()}}
        envelope.update(overrides)
        body = json.dumps(envelope).encode()
        return self.signed_headers(body), body

    def test_valid_notification_verified_and_decrypted_without_changing_state(self):
        headers, body = self.notification()
        first = self.client.verify_notification(headers, body)
        second = self.client.verify_notification(headers, body)
        self.assertEqual(first, second)  # Database idempotency is the core's responsibility.
        self.assertEqual(first['resource']['amount']['total'], 800)
        self.assertEqual(first['id'], 'notify-unit-test')
        self.transport.open.assert_not_called()

    def test_refund_notification_does_not_require_appid_and_preserves_processing_state(self):
        headers, body = self.notification(
            resource={'mchid': self.config['mchid'], 'out_trade_no': 'ORDER123456',
                      'out_refund_no': 'REFUND123', 'refund_status': 'SUCCESS',
                      'amount': {'refund': 800, 'total': 800, 'currency': 'CNY'}},
            event_type='REFUND.SUCCESS')
        result = self.client.verify_notification(headers, body)
        self.assertEqual(result['event_type'], 'REFUND.SUCCESS')
        self.assertEqual(result['resource']['refund_status'], 'SUCCESS')
        self.assertNotIn('appid', result['resource'])

    def test_signed_notification_wrong_account_and_bad_aes_tag_rejected(self):
        for resource in ({'mchid': '1900000999'}, {'mchid': self.config['mchid'], 'appid': 'otherappid'}):
            headers, body = self.notification(resource=resource)
            with self.assertRaises(GatewayError):
                self.client.verify_notification(headers, body)
        headers, body = self.notification()
        envelope = json.loads(body)
        envelope['resource']['ciphertext'] = base64.b64encode(b'0' * 64).decode()
        body = json.dumps(envelope).encode()
        with self.assertRaises(GatewayError):
            self.client.verify_notification(self.signed_headers(body), body)

    def test_tampered_body_and_duplicate_json_keys_rejected(self):
        headers, body = self.notification()
        with self.assertRaises(GatewayError):
            self.client.verify_notification(headers, body + b' ')
        duplicate = b'{"trade_state":"NOTPAY","trade_state":"SUCCESS"}'
        self.response(raw=duplicate)
        with self.assertRaises(GatewayError):
            self.client.query_payment('ORDER123456')

    def test_full_refund_uses_stable_number_and_does_not_convert_processing_to_success(self):
        self.response({'status': 'PROCESSING', 'out_refund_no': 'REFUND123'})
        result = self.client.refund(out_trade_no='ORDER123456', out_refund_no='REFUND123', amount_cents=800)
        self.assertEqual(result['status'], 'PROCESSING')
        request = self.transport.open.call_args.args[0]
        self.assertEqual(json.loads(request.data)['amount'], {'refund': 800, 'total': 800, 'currency': 'CNY'})
        self.assertEqual(json.loads(request.data)['out_refund_no'], 'REFUND123')
        self.response({'status': 'SUCCESS'})
        self.assertEqual(self.client.query_refund('REFUND123')['status'], 'SUCCESS')

    def test_missing_configuration_private_callback_and_partner_mode_fail_closed(self):
        for changed in ({'notify_url': 'http://10.53.8.40/pay'}, {'notify_url': 'https://127.0.0.1/pay'},
                        {'notify_url': 'https://example.com/pay?account=1'}, {'mode': 'partner'},
                        {'payment_public_keys': {}}, {'api_v3_key': ''}, {'serial_no': 'bad\nheader'},
                        {'private_key_path': 'missing-secret-path'}, {'channels': ['jsapi']}):
            with self.subTest(fields=list(changed)):
                with self.assertRaises(ConfigurationError) as raised:
                    WechatPayClient({**self.config, **changed})
                self.assertNotIn('missing-secret-path', str(raised.exception))
        self.transport.open.assert_not_called()

    def test_disabled_account_can_reconcile_but_cannot_create(self):
        self.client = WechatPayClient({**self.config, 'enabled': False})
        with self.assertRaises(ConfigurationError):
            self.create()
        self.transport.open.assert_not_called()
        self.response({'trade_state': 'SUCCESS'})
        self.assertEqual(self.client.query_payment('ORDER123456')['trade_state'], 'SUCCESS')

    def test_arguments_and_unsupported_jsapi_never_reach_network(self):
        for changed in ({'channel': 'jsapi'}, {'amount_cents': True}, {'amount_cents': 8.0},
                        {'out_trade_no': '../bad-path'}, {'description': ''},
                        {'expires_at': datetime.now(timezone.utc) + timedelta(seconds=30)},
                        {'expires_at': datetime.now()}):
            with self.subTest(fields=list(changed)):
                with self.assertRaises(GatewayError) as raised:
                    self.create(**changed)
                self.assertFalse(raised.exception.outcome_unknown)
        self.transport.open.assert_not_called()

    def test_malicious_signed_redirect_urls_rejected(self):
        for url in ('https://wx.tenpay.com.evil.example/pay', 'javascript:alert(1)',
                    'https://wx.tenpay.com:garbage/pay', 'https://secret@wx.tenpay.com/pay'):
            self.response({'h5_url': url})
            with self.assertRaises(GatewayError):
                self.create(channel='h5', payer_client_ip='203.0.113.7')
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.example'))
