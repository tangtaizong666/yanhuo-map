"""Account request contracts use Django's isolated test database only."""
import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import Event


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class JSONObjectContractTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('contract_reader', password='OriginalRain!2026')
        self.api = APIClient()

    def test_login_rejects_non_object_json_before_authentication(self):
        for value in ([], ['unexpected'], 'unexpected', 7, True, None):
            with self.subTest(value=value), patch('market.views.authenticate') as authenticate:
                response = self.api.post('/api/v1/auth/login', json.dumps(value),
                    content_type='application/json')
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data['code'], 'invalid_json_object')
                authenticate.assert_not_called()

    def test_authenticated_account_and_order_actions_reject_non_object_json(self):
        self.api.force_authenticate(self.user)
        missing_order = '00000000-0000-0000-0000-000000000000'
        endpoints = (
            ('patch', '/api/v1/auth/profile'),
            ('post', '/api/v1/auth/password'),
            ('delete', '/api/v1/auth/account'),
            ('post', '/api/v1/auth/recovery'),
            ('post', f'/api/v1/orders/{missing_order}/cancel'),
            ('post', f'/api/v1/orders/{missing_order}/payments/wechat'),
            ('post', f'/api/v1/merchant/orders/{missing_order}/action'),
        )
        for method, path in endpoints:
            with self.subTest(path=path):
                response = getattr(self.api, method)(path, '[]', content_type='application/json')
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data['code'], 'invalid_json_object')
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertEqual(self.user.first_name, '')
        self.assertTrue(self.user.check_password('OriginalRain!2026'))

    def test_nested_objects_keep_the_existing_event_contract(self):
        response = self.api.post('/api/v1/events', {
            'type': 'home_view', 'metadata': {'source': 'home'},
        }, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Event.objects.get().metadata, {'source': 'home'})

    def test_empty_objects_and_malformed_json_keep_validation_responses(self):
        response = self.api.post('/api/v1/auth/login', {}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['code'], 'invalid_credentials')
        response = self.api.post('/api/v1/auth/login', '{', content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertNotEqual(response.data.get('code'), 'invalid_json_object')

    @override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=128)
    def test_body_limit_is_checked_before_json_shape(self):
        response = self.api.post('/api/v1/auth/login', json.dumps(['x' * 128]),
            content_type='application/json')
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.data['code'], 'request_too_large')


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class PasswordWhitespaceContractTests(TestCase):
    original = 'OriginalRain!2026'
    padded = '  PaddedRain!2027\t'

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('password_reader', password=self.original)
        self.api = APIClient()
        self.api.force_login(self.user)

    def recovery_code(self):
        response = self.api.post('/api/v1/auth/recovery', {'password': self.original}, format='json')
        self.assertEqual(response.status_code, 200)
        return response.data['recovery_code']

    def reset(self, code, password):
        return APIClient().post('/api/v1/auth/recovery/reset', {
            'username': self.user.username, 'recovery_code': code, 'new_password': password,
        }, format='json')

    def _verified_code(self, phone='13900139001'):
        from .registration_verify import issue_captcha
        captcha_id, captcha_code, _ = issue_captcha()
        response = APIClient().post('/api/v1/auth/register/code', {
            'phone': phone, 'captcha_id': captcha_id, 'captcha_code': captcha_code,
        }, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data['demo_code']

    def test_registration_preserves_password_for_subsequent_login(self):
        sms_code = self._verified_code('13900139001')
        response = APIClient().post('/api/v1/auth/register', {
            'username': 'registered_reader', 'display_name': '读者同学',
            'phone': '13900139001', 'verification_code': sms_code, 'password': self.padded,
        }, format='json')
        self.assertEqual(response.status_code, 201)
        registered = User.objects.get(username='registered_reader')
        self.assertTrue(registered.check_password(self.padded))
        self.assertFalse(registered.check_password(self.padded.strip()))
        response = APIClient().post('/api/v1/auth/login', {
            'username': registered.username, 'password': self.padded,
        }, format='json')
        self.assertEqual(response.status_code, 200)

    def test_regular_password_change_preserves_new_password(self):
        response = self.api.post('/api/v1/auth/password', {
            'old_password': self.original, 'new_password': self.padded,
        }, format='json')
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.padded))
        self.assertFalse(self.user.check_password(self.padded.strip()))

    def test_recovered_padded_password_can_be_changed_again(self):
        self.assertEqual(self.reset(self.recovery_code(), self.padded).status_code, 200)
        self.assertTrue(self.api.login(username=self.user.username, password=self.padded))
        response = self.api.post('/api/v1/auth/password', {
            'old_password': self.padded, 'new_password': 'AnotherRain!2028',
        }, format='json')
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('AnotherRain!2028'))

    def test_padded_password_can_confirm_account_deletion(self):
        self.user.set_password(self.padded)
        self.user.save(update_fields=['password'])
        self.api.force_login(self.user)
        response = self.api.delete('/api/v1/auth/account', {'password': self.padded}, format='json')
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)

    def test_registration_rejects_empty_and_whitespace_only_passwords(self):
        sms_code = self._verified_code('13900139002')
        for password in ('', ' \t\n' * 4):
            with self.subTest(password=password):
                response = APIClient().post('/api/v1/auth/register', {
                    'username': 'blank_reader', 'display_name': '空白密码测试',
                    'phone': '13900139002', 'verification_code': sms_code, 'password': password,
                }, format='json')
                self.assertEqual(response.status_code, 400)
                self.assertFalse(User.objects.filter(username='blank_reader').exists())

    def test_registration_verification_enforces_captcha_code_and_unique_phone(self):
        client = APIClient()
        cap = client.get('/api/v1/auth/captcha')
        self.assertEqual(cap.status_code, 200)
        self.assertTrue(cap.data['svg'].startswith('data:image/svg+xml;utf8,'))
        # Wrong graphic captcha consumes challenge and rejects
        bad_cap = client.post('/api/v1/auth/register/code', {
            'phone': '13712345678', 'captcha_id': cap.data['captcha_id'], 'captcha_code': 'ZZZZ',
        }, format='json')
        self.assertEqual(bad_cap.status_code, 400)
        self.assertEqual(bad_cap.data['code'], 'invalid_captcha')

        sms_code = self._verified_code('13712345678')
        # Wrong SMS code fails without creating user
        bad_sms = client.post('/api/v1/auth/register', {
            'username': 'verified_one', 'display_name': '实名同学',
            'phone': '13712345678', 'verification_code': '000000' if sms_code != '000000' else '111111',
            'password': self.original,
        }, format='json')
        self.assertEqual(bad_sms.status_code, 400)
        self.assertEqual(bad_sms.data['code'], 'invalid_verification_code')

        ok = client.post('/api/v1/auth/register', {
            'username': 'verified_one', 'display_name': '实名同学',
            'phone': '13712345678', 'verification_code': sms_code, 'password': self.original,
        }, format='json')
        self.assertEqual(ok.status_code, 201)
        self.assertEqual(ok.data['masked_phone'], '137****5678')

        # Duplicate phone cannot request a second registration code
        from .registration_verify import issue_captcha
        cid, ccode, _ = issue_captcha()
        dup = APIClient().post('/api/v1/auth/register/code', {
            'phone': '13712345678', 'captcha_id': cid, 'captcha_code': ccode,
        }, format='json')
        self.assertEqual(dup.status_code, 400)
        self.assertEqual(dup.data['code'], 'phone_taken')

        # Deleting the account releases the verified phone hash cleanly
        self.assertEqual(client.delete('/api/v1/auth/account', {'password': self.original}, format='json').status_code, 200)
        re_code = self._verified_code('13712345678')
        re_ok = APIClient().post('/api/v1/auth/register', {
            'username': 'verified_two', 'display_name': '重新注册同学',
            'phone': '13712345678', 'verification_code': re_code, 'password': self.original,
        }, format='json')
        self.assertEqual(re_ok.status_code, 201)

    @override_settings(DEMO_MODE=False)
    def test_non_demo_mode_never_returns_demo_code(self):
        from .registration_verify import issue_captcha
        cap_res = APIClient().get('/api/v1/auth/captcha')
        self.assertEqual(cap_res.status_code, 200)
        self.assertNotIn('demo_captcha_code', cap_res.data)
        cid, ccode, _ = issue_captcha()
        res = APIClient().post('/api/v1/auth/register/code', {
            'phone': '13688889999', 'captcha_id': cid, 'captcha_code': ccode,
        }, format='json')
        self.assertEqual(res.status_code, 503)
        self.assertNotIn('demo_code', res.data)

    def test_password_change_rejects_empty_and_whitespace_only_passwords(self):
        for password in ('', ' \t\n' * 4):
            with self.subTest(password=password):
                response = self.api.post('/api/v1/auth/password', {
                    'old_password': self.original, 'new_password': password,
                }, format='json')
                self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.original))

    def test_whitespace_only_recovery_password_does_not_consume_code(self):
        code = self.recovery_code()
        self.assertEqual(self.reset(code, ' \t\n' * 4).status_code, 400)
        self.assertEqual(self.reset(code, 'NextRain!2028').status_code, 200)

    def test_length_and_weak_password_checks_remain_in_force(self):
        for password in ('short', '12345678', 'x' * 129):
            with self.subTest(password=password):
                response = self.api.post('/api/v1/auth/password', {
                    'old_password': self.original, 'new_password': password,
                }, format='json')
                self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.original))
