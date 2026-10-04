"""A stale tab cannot apply its draft under another tab's newer session cookie."""
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import Order
from .tests import fixtures, payload


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class SessionBoundaryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.student, self.other, self.vendor, self.stall, self.product = fixtures()
        self.api = APIClient()

    def test_new_cookie_cannot_submit_previous_account_cart_or_consume_stock(self):
        self.api.force_login(self.student)
        expected = str(self.api.get('/api/v1/auth/me').json()['id'])
        # A second tab replaces the shared cookie after /auth/me was handled.
        self.api.force_login(self.other)
        response = self.api.post('/api/v1/orders', payload(self.stall, self.product),
            format='json', HTTP_X_YANHUO_ACTOR=expected)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['code'], 'session_changed')
        self.assertIs(response.json()['submitted'], False)
        self.assertNotIn('user', response.json())
        self.assertFalse(Order.objects.exists())
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 5)

    def test_matching_actor_and_legacy_clients_keep_existing_order_behavior(self):
        for user, headers in ((self.student, {'HTTP_X_YANHUO_ACTOR': str(self.student.pk)}),
                              (self.other, {})):
            self.api.force_login(user)
            response = self.api.post('/api/v1/orders', payload(self.stall, self.product), format='json', **headers)
            self.assertEqual(response.status_code, 201, response.content)
            self.assertTrue(Order.objects.filter(pk=response.json()['id'], user=user).exists())

    def test_logout_and_malformed_actor_reject_before_business_mutation(self):
        self.api.force_login(self.student)
        for actor in ('anonymous', 'invalid', str(self.other.pk)):
            response = self.api.post('/api/v1/orders', payload(self.stall, self.product),
                format='json', HTTP_X_YANHUO_ACTOR=actor)
            self.assertEqual(response.status_code, 409)
        self.api.logout()
        response = self.api.post('/api/v1/orders', payload(self.stall, self.product),
            format='json', HTTP_X_YANHUO_ACTOR=str(self.student.pk))
        self.assertEqual(response.status_code, 409)
        self.assertFalse(Order.objects.exists())

    def test_actor_header_never_authenticates_or_grants_merchant_permissions(self):
        response = self.api.post('/api/v1/orders', payload(self.stall, self.product),
            format='json', HTTP_X_YANHUO_ACTOR='anonymous')
        self.assertEqual(response.status_code, 403)
        self.api.force_login(self.other)
        response = self.api.patch(f'/api/v1/merchant/products/{self.product.pk}',
            {'name': 'must not change'}, format='json', HTTP_X_YANHUO_ACTOR=str(self.other.pk))
        self.assertEqual(response.status_code, 404)
        self.product.refresh_from_db()
        self.assertNotEqual(self.product.name, 'must not change')

    def test_actor_header_does_not_replace_csrf(self):
        api = APIClient(enforce_csrf_checks=True)
        api.force_login(self.student)
        response = api.post('/api/v1/orders', payload(self.stall, self.product),
            format='json', HTTP_X_YANHUO_ACTOR=str(self.student.pk))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Order.objects.exists())

    def test_public_reads_and_anonymous_login_are_unchanged(self):
        response = self.api.get('/api/v1/stalls', HTTP_X_YANHUO_ACTOR='old-tab')
        self.assertEqual(response.status_code, 200)
        self.student.set_password('TestAccountPassword42!'); self.student.save()
        response = self.api.post('/api/v1/auth/login', {
            'username': self.student.username, 'password': 'TestAccountPassword42!',
        }, format='json', HTTP_X_YANHUO_ACTOR='anonymous')
        self.assertEqual(response.status_code, 200, response.content)

