import json
from unittest.mock import patch
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from .models import AccountRecovery, AuditLog, Area, MerchantProfile, Stall, Order, OrderItem, Product


class RecoveryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.password = 'RiverCampus!2026'
        self.user = User.objects.create_user('recover_student', password=self.password)
        self.client = APIClient()
        self.client.force_login(self.user)

    def generate(self):
        response = self.client.post('/api/v1/auth/recovery', {'password': self.password}, format='json')
        self.assertEqual(response.status_code, 200)
        return response.json()['recovery_code']

    def reset(self, code, password='NewRiverSun!2027', username=None, client=None):
        return (client or APIClient()).post('/api/v1/auth/recovery/reset', {
            'username': username or self.user.username, 'recovery_code': code, 'new_password': password}, format='json')

    def test_generate_requires_current_password_and_never_persists_plaintext(self):
        self.assertEqual(self.client.post('/api/v1/auth/recovery', {'password': 'wrong'}, format='json').status_code, 400)
        self.assertFalse(AccountRecovery.objects.exists())
        code = self.generate()
        record = AccountRecovery.objects.get(user=self.user)
        self.assertNotEqual(record.code_hash, code.replace('-', ''))
        read = self.client.get('/api/v1/auth/recovery')
        self.assertEqual(read.json()['enabled'], True)
        self.assertNotIn('recovery_code', read.json())
        self.assertEqual(read['Cache-Control'], 'no-store, private')
        self.assertNotIn(code, json.dumps(list(AuditLog.objects.values('details'))))

    def test_reset_consumes_code_and_invalidates_other_sessions(self):
        other = APIClient(); other.login(username=self.user.username, password=self.password)
        code = self.generate()
        self.assertEqual(self.reset(code.lower().replace('-', ' ')).status_code, 200)
        self.user.refresh_from_db(); self.assertTrue(self.user.check_password('NewRiverSun!2027'))
        self.assertEqual(self.reset(code, 'ThirdRiverSun!2028').status_code, 400)
        self.assertIsNone(other.get('/api/v1/auth/me').json())
        self.assertIsNone(self.client.get('/api/v1/auth/me').json())
        self.assertTrue(AccountRecovery.objects.get(user=self.user).used_at)

    def test_rotation_and_revocation_invalidate_previous_codes(self):
        old = self.generate(); current = self.generate()
        self.assertEqual(self.reset(old).status_code, 400)
        self.assertEqual(self.client.delete('/api/v1/auth/recovery', {'password': self.password}, format='json').status_code, 200)
        self.assertEqual(self.reset(current).status_code, 400)
        self.assertFalse(self.client.get('/api/v1/auth/recovery').json()['enabled'])

    def test_unknown_user_and_wrong_code_have_same_public_error(self):
        self.generate()
        a = self.reset('A'*32)
        b = self.reset('A'*32, username='does_not_exist')
        self.assertEqual(a.status_code, 400)
        self.assertEqual(a.json(), b.json())

    def test_weak_or_unchanged_password_does_not_consume_code(self):
        code = self.generate()
        self.assertEqual(self.reset(code, '12345678').status_code, 400)
        self.assertEqual(self.reset(code, self.password).status_code, 400)
        self.assertEqual(self.reset(code).status_code, 200)

    def test_regular_password_change_invalidates_code(self):
        code = self.generate()
        response = self.client.post('/api/v1/auth/password', {'old_password': self.password, 'new_password': 'OtherSunshine!2027'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.client.get('/api/v1/auth/recovery').json()['enabled'])
        self.assertEqual(self.reset(code).status_code, 400)

    def test_fresh_locked_user_prevents_stale_password_overwrite(self):
        self.generate()
        changed = User.objects.get(pk=self.user.pk)
        changed.set_password('AlreadyRecovered!2027'); changed.save()
        # Simulate a request authenticated just before the concurrent reset.
        self.client.force_authenticate(self.user)
        response = self.client.post('/api/v1/auth/password', {'old_password': self.password, 'new_password': 'ShouldNotWin!2028'}, format='json')
        self.assertEqual(response.status_code, 400)
        changed.refresh_from_db(); self.assertTrue(changed.check_password('AlreadyRecovered!2027'))

    def test_recovery_requires_csrf_and_authenticated_management(self):
        self.assertEqual(APIClient().get('/api/v1/auth/recovery').status_code, 403)
        protected = APIClient(enforce_csrf_checks=True)
        self.assertEqual(self.reset('A'*32, client=protected).status_code, 403)
        protected.force_login(self.user)
        self.assertEqual(protected.post('/api/v1/auth/recovery', {'password': self.password}, format='json').status_code, 403)

    def test_invalid_or_inactive_account_cannot_recover(self):
        code = self.generate()
        self.user.is_active = False; self.user.save()
        self.assertEqual(self.reset(code).status_code, 400)

    def test_reset_requests_are_throttled(self):
        with patch('market.views.AuthThrottle.THROTTLE_RATES', {'auth': '2/hour'}):
            client = APIClient()
            self.assertEqual(self.reset('A'*32, client=client).status_code, 400)
            self.assertEqual(self.reset('A'*32, client=client).status_code, 400)
            self.assertEqual(self.reset('A'*32, client=client).status_code, 429)

    def test_spoofed_forwarded_headers_cannot_bypass_auth_throttle(self):
        with self.settings(AUTH_TRUST_PROXY_CLIENT_IP=False), patch('market.views.AuthThrottle.THROTTLE_RATES', {'auth': '2/hour'}):
            client = APIClient()
            for index in range(3):
                response = client.post('/api/v1/auth/recovery/reset', {'username': self.user.username, 'recovery_code': 'A'*32, 'new_password': 'NextSecret!2027'}, format='json',
                    HTTP_X_FORWARDED_FOR=f'10.0.0.{index}', HTTP_X_REAL_IP=f'10.1.0.{index}')
                self.assertEqual(response.status_code, 429 if index == 2 else 400)

    def test_deactivation_scrubs_portion_notes_and_recovery(self):
        self.generate()
        owner = User.objects.create_user('owner')
        area = Area.objects.create(name='area', latitude=31, longitude=121)
        merchant = MerchantProfile.objects.create(user=owner, business_name='stall')
        stall = Stall.objects.create(merchant=merchant, area=area, name='stall', category='小吃')
        product = Product.objects.create(stall=stall, name='food', price_cents=100)
        order = Order.objects.create(user=self.user, stall=stall, stall_name='stall', status='cancelled', total_cents=100,
            pickup_address='gate', pickup_latitude=31, pickup_longitude=121, idempotency_key='privacy-test', request_hash='test', expires_at=timezone.now())
        item = OrderItem.objects.create(order=order, product=product, name='food', unit_price_cents=100, quantity=1,
            portions=[{'options': {'辣度': '少辣'}, 'note': '请打电话13800138000'}])
        response = self.client.delete('/api/v1/auth/account', {'password': self.password}, format='json')
        self.assertEqual(response.status_code, 200)
        item.refresh_from_db(); self.assertEqual(item.portions, [{'options': {'辣度': '少辣'}, 'note': ''}])
        self.assertFalse(AccountRecovery.objects.get(user=self.user).code_hash)

    def test_planned_hours_do_not_change_state_or_location(self):
        from .models import BusinessSession
        from django.utils import timezone
        owner = MerchantProfile.objects.create(user=self.user, business_name='owner')
        area = Area.objects.create(name='area', latitude=31, longitude=121)
        stall = Stall.objects.create(merchant=owner, area=area, name='stall', category='小吃')
        session = BusinessSession.objects.create(stall=stall, status='closed', last_confirmed_at=timezone.now())
        stall.current_session = session; stall.save()
        previous = session.last_confirmed_at
        response = self.client.patch(f'/api/v1/merchant/stalls/{stall.pk}/profile', {'usual_hours': '工作日17:00–21:00'}, format='json')
        self.assertEqual(response.status_code, 200)
        stall.refresh_from_db(); session.refresh_from_db()
        self.assertEqual(stall.usual_hours, '工作日17:00–21:00')
        self.assertEqual(stall.effective_status(), 'closed'); self.assertEqual(session.last_confirmed_at, previous)
        self.assertEqual(self.client.patch(f'/api/v1/merchant/stalls/{stall.pk}/profile', {'usual_hours': 'x'*101}, format='json').status_code, 400)
