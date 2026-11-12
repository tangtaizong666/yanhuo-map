"""Security regressions use isolated databases and temporary media only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event as ThreadEvent
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import authenticate
from django.contrib.auth.models import AnonymousUser, Permission, User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.uploadhandler import StopUpload
from django.db import close_old_connections, connection, connections, transaction
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.request import Request
from rest_framework.test import APIClient

from .auth_limits import TrustedAnonRateThrottle, client_ip, login_attempt, LoginRateLimited
from .models import AuditLog, PaymentAttempt, PaymentRefund, Product
from .operations import FeedbackThrottle
from .security_models import AuthenticationFailureBucket, ProductCreation
from .services import create_order
from .tests import fixtures, payload
from .upload_handlers import BoundedImageUploadHandler
from .security_cleanup import cleanup_authentication_buckets


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class MerchantPermissionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.student, self.other, self.vendor, self.stall, self.product = fixtures()
        self.viewer = User.objects.create_user('limited_viewer', is_staff=True)
        self.viewer.user_permissions.add(Permission.objects.get(codename='view_merchantapplication'))
        self.api = APIClient()
        self.api.force_authenticate(self.viewer)

    def test_read_only_staff_cannot_read_or_mutate_other_merchants(self):
        order, _ = create_order(self.student, payload(self.stall, self.product))
        self.assertEqual(self.api.get('/api/v1/merchant/stalls').data, [])
        self.assertEqual(self.api.get('/api/v1/merchant/orders').data, [])
        self.assertEqual(self.api.get('/api/v1/merchant/metrics').status_code, 403)
        self.assertEqual(self.api.patch(f'/api/v1/merchant/products/{self.product.pk}',
            {'name': 'unauthorized'}, format='json').status_code, 404)
        self.assertEqual(self.api.post(f'/api/v1/merchant/stalls/{self.stall.pk}/restock',
            {'idempotency_key': 'denied-restock', 'items': [{'product_id': self.product.pk, 'quantity': 1}]},
            format='json').status_code, 404)
        self.assertEqual(self.api.post(f'/api/v1/merchant/orders/{order.pk}/action',
            {'action': 'accept'}, format='json').status_code, 404)
        self.product.refresh_from_db()
        self.assertNotEqual(self.product.name, 'unauthorized')

    def test_explicit_operator_permissions_are_operation_specific(self):
        self.viewer.user_permissions.add(Permission.objects.get(codename='change_product'))
        # Permission caches belong to a request user; refresh the test principal.
        self.api.force_authenticate(User.objects.get(pk=self.viewer.pk))
        self.assertEqual(self.api.patch(f'/api/v1/merchant/products/{self.product.pk}',
            {'name': 'authorized'}, format='json').status_code, 200)
        self.assertEqual(self.api.patch(f'/api/v1/merchant/stalls/{self.stall.pk}/profile',
            {'name': 'forbidden'}, format='json').status_code, 404)
        self.assertEqual(self.api.post(f'/api/v1/merchant/stalls/{self.stall.pk}/products',
            {'name': 'forbidden', 'price_cents': 500}, format='json').status_code, 404)

    def test_ownership_and_superuser_remain_valid(self):
        for user in [self.vendor, User.objects.create_superuser('security_admin', password='SafePassword!2026')]:
            self.api.force_authenticate(user)
            self.assertEqual(self.api.patch(f'/api/v1/merchant/products/{self.product.pk}',
                {'description': 'allowed'}, format='json').status_code, 200)
        self.api.force_authenticate(self.other)
        self.assertEqual(self.api.patch(f'/api/v1/merchant/products/{self.product.pk}',
            {'description': 'denied'}, format='json').status_code, 404)


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    AUTH_FAILURE_WINDOW_SECONDS=900, AUTH_FAILURE_ACCOUNT_LIMIT=3, AUTH_FAILURE_IP_LIMIT=20)
class LoginProtectionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('login_user', password='SecretExample!2026', is_staff=True)
        self.api = APIClient(enforce_csrf_checks=True)
        self.api.get('/api/v1/auth/csrf')

    def attempt(self, username='login_user', password='incorrect', **headers):
        return self.api.post('/api/v1/auth/login', {'username': username, 'password': password},
            format='json', HTTP_X_CSRFTOKEN=self.api.cookies['csrftoken'].value, **headers)

    def admin_attempt(self, password='incorrect'):
        return self.api.post('/admin/login/', {'username': 'login_user', 'password': password,
            'csrfmiddlewaretoken': self.api.cookies['csrftoken'].value, 'next': '/admin/'}, format='multipart')

    def test_api_and_admin_share_failures_and_stop_password_work(self):
        self.assertEqual(self.attempt().status_code, 400)
        self.assertEqual(self.admin_attempt().status_code, 200)
        self.assertEqual(self.attempt().status_code, 400)
        with patch('market.views.authenticate', wraps=authenticate) as backend:
            denied = self.attempt()
            self.assertEqual(denied.status_code, 429)
            self.assertIn('Retry-After', denied)
            backend.assert_not_called()
        denied = self.admin_attempt()
        self.assertEqual(denied.status_code, 429)
        self.assertIn('Retry-After', denied)
        self.assertEqual(list(AuthenticationFailureBucket.objects.values_list('failures', flat=True)), [3, 3])

    def test_success_does_not_consume_failure_budget(self):
        self.assertEqual(self.attempt().status_code, 400)
        for _ in range(5):
            self.assertEqual(self.attempt(password='SecretExample!2026').status_code, 200)
        self.assertEqual(sorted(AuthenticationFailureBucket.objects.values_list('failures', flat=True)), [0, 1])

    @override_settings(AUTH_FAILURE_ACCOUNT_LIMIT=10, AUTH_FAILURE_IP_LIMIT=3, AUTH_TRUST_PROXY_CLIENT_IP=True)
    def test_ip_limits_password_spraying_but_isolates_other_clients(self):
        for username in ['missing_one', 'missing_two', 'missing_three']:
            self.assertEqual(self.attempt(username, HTTP_X_REAL_IP='198.51.100.1').status_code, 400)
        self.assertEqual(self.attempt('missing_four', HTTP_X_REAL_IP='198.51.100.1').status_code, 429)
        self.assertEqual(self.attempt('missing_five', HTTP_X_REAL_IP='198.51.100.2').status_code, 400)

    def test_window_expires_and_identifiers_are_hashed(self):
        for _ in range(3):
            self.assertEqual(self.attempt().status_code, 400)
        self.assertEqual(self.attempt().status_code, 429)
        AuthenticationFailureBucket.objects.update(window_started_at=timezone.now() - timedelta(seconds=901))
        self.assertEqual(self.attempt().status_code, 400)
        for row in AuthenticationFailureBucket.objects.all():
            self.assertEqual(row.failures, 1)
            self.assertEqual(len(row.key), 64)
            self.assertNotIn('login_user', row.key)

    def test_csrf_failure_does_not_consume_password_budget(self):
        response = self.api.post('/api/v1/auth/login', {'username': 'login_user', 'password': 'incorrect'}, format='json')
        self.assertEqual(response.status_code, 403)
        self.assertFalse(AuthenticationFailureBucket.objects.exists())


class TrustedClientIPTests(TestCase):
    def request(self, ip, **headers):
        request = Request(RequestFactory().get('/', REMOTE_ADDR=ip, **headers))
        request.user = AnonymousUser()
        return request

    @override_settings(AUTH_TRUST_PROXY_CLIENT_IP=False)
    def test_untrusted_forwarding_headers_cannot_change_buckets(self):
        one = self.request('127.0.0.1', HTTP_X_FORWARDED_FOR='198.51.100.1', HTTP_X_REAL_IP='198.51.100.1')
        two = self.request('127.0.0.1', HTTP_X_FORWARDED_FOR='198.51.100.2', HTTP_X_REAL_IP='198.51.100.2')
        self.assertEqual(client_ip(one), client_ip(two))
        for throttle in [TrustedAnonRateThrottle(), FeedbackThrottle()]:
            self.assertEqual(throttle.get_cache_key(one, None), throttle.get_cache_key(two, None))

    @override_settings(AUTH_TRUST_PROXY_CLIENT_IP=True)
    def test_proxy_clients_get_independent_buckets_and_invalid_ip_falls_back(self):
        one = self.request('172.20.0.3', HTTP_X_REAL_IP='198.51.100.1')
        two = self.request('172.20.0.3', HTTP_X_REAL_IP='198.51.100.2')
        for throttle in [TrustedAnonRateThrottle(), FeedbackThrottle()]:
            self.assertNotEqual(throttle.get_cache_key(one, None), throttle.get_cache_key(two, None))
        self.assertEqual(client_ip(self.request('172.20.0.3', HTTP_X_REAL_IP='untrusted')), '172.20.0.3')

    @override_settings(AUTH_TRUST_PROXY_CLIENT_IP=True)
    def test_payment_proxy_override_is_independent_of_auth_setting(self):
        request = self.request('172.20.0.3', HTTP_X_REAL_IP='198.51.100.1')
        self.assertEqual(client_ip(request, trust_proxy=False), '172.20.0.3')
        self.assertEqual(client_ip(request, trust_proxy=True), '198.51.100.1')
        self.assertEqual(client_ip(self.request('172.20.0.3', HTTP_X_REAL_IP='invalid'), trust_proxy=True), '172.20.0.3')


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class AccountDeletionProtectionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.student, self.other, self.vendor, self.stall, self.product = fixtures()
        self.api = APIClient()
        self.api.force_authenticate(self.student)

    def delete_account(self):
        return self.api.delete('/api/v1/auth/account', {'password': 'DemoStrong123'}, format='json')

    def terminal_order(self):
        order, _ = create_order(self.student, payload(self.stall, self.product))
        order.status = 'cancelled'
        order.save(update_fields=['status'])
        return order

    def payment(self, order, status):
        return PaymentAttempt.objects.create(order=order, merchant=self.stall.merchant,
            account_key='test-account', mchid='test-mchid', appid='test-appid',
            out_trade_no='account-deletion-payment', channel='native', status=status,
            transaction_id='captured-transaction' if status == 'paid' else None,
            amount_cents=order.total_cents, expires_at=timezone.now()+timedelta(minutes=10))

    def test_normal_account_without_orders_can_be_deactivated(self):
        self.assertEqual(self.delete_account().status_code, 200)
        self.student.refresh_from_db()
        self.assertFalse(self.student.is_active)

    def test_terminal_order_with_active_payment_prevents_deactivation(self):
        self.payment(self.terminal_order(), 'reconcile')
        self.assertEqual(self.delete_account().status_code, 409)
        self.student.refresh_from_db()
        self.assertTrue(self.student.is_active)

    def test_terminal_order_with_review_flag_prevents_deactivation(self):
        order = self.terminal_order()
        order.payment_review_required = True
        order.save(update_fields=['payment_review_required'])
        self.assertEqual(self.delete_account().status_code, 409)

    def test_closed_refund_remains_protected_until_verified_resolution(self):
        order = self.terminal_order()
        payment = self.payment(order, 'paid')
        refund = PaymentRefund.objects.create(order=order, payment=payment, status='closed',
            out_refund_no='account-deletion-refund', amount_cents=order.total_cents, reason='test refund')
        self.assertEqual(self.delete_account().status_code, 409)
        refund.resolved_at = timezone.now()
        refund.save(update_fields=['resolved_at'])
        self.assertEqual(self.delete_account().status_code, 200)


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ProductCreationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.student, self.other, self.vendor, self.stall, self.product = fixtures()
        self.api = APIClient()
        self.api.force_authenticate(self.vendor)
        self.url = f'/api/v1/merchant/stalls/{self.stall.pk}/products'
        self.data = {'name': '幂等新品', 'price_cents': 600, 'stock': 10, 'idempotency_key': 'creation-intent-123'}

    def test_retry_creates_exactly_one_product_and_audit(self):
        first = self.api.post(self.url, self.data, format='json')
        replay = self.api.post(self.url, self.data, format='json')
        self.assertEqual(first.status_code, 201)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(first.data['id'], replay.data['id'])
        self.assertEqual(Product.objects.filter(name=self.data['name']).count(), 1)
        self.assertEqual(ProductCreation.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(action='product_created').count(), 1)

    def test_conflict_and_permission_are_checked_before_replay(self):
        self.api.post(self.url, self.data, format='json')
        response = self.api.post(self.url, {**self.data, 'price_cents': 700}, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'idempotency_conflict')
        self.api.force_authenticate(self.other)
        self.assertEqual(self.api.post(self.url, self.data, format='json').status_code, 404)

    def test_deleted_product_cannot_be_silently_recreated(self):
        first = self.api.post(self.url, self.data, format='json')
        Product.objects.get(pk=first.data['id']).delete()
        response = self.api.post(self.url, self.data, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'idempotency_resource_gone')

    def test_old_clients_and_product_patch_remain_compatible(self):
        data = {key: value for key, value in self.data.items() if key != 'idempotency_key'}
        self.assertEqual(self.api.post(self.url, data, format='json').status_code, 201)
        self.assertEqual(self.api.patch(f'/api/v1/merchant/products/{self.product.pk}',
            {'idempotency_key': 'not-for-edit'}, format='json').status_code, 400)


class UploadBoundaryTests(TestCase):
    @override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
    def test_eps_is_rejected_before_its_format_plugin_parses_it(self):
        _, _, vendor, stall, _ = fixtures()
        client = APIClient()
        client.force_authenticate(vendor)
        eps = b'%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 100 100\n%%EndComments\nshowpage\n'
        with patch('PIL.EpsImagePlugin.EpsImageFile._open', side_effect=AssertionError('EPS parser must never run')) as eps_parser:
            response = client.post(f'/api/v1/merchant/stalls/{stall.pk}/image',
                {'file': SimpleUploadedFile('unexpected.eps', eps, content_type='image/jpeg')}, format='multipart')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['code'], 'invalid_image')
        eps_parser.assert_not_called()

    def test_oversized_drf_json_is_rejected_before_login_authentication(self):
        client = APIClient()
        body = '{"username":"' + 'x' * (6 * 1024 * 1024) + '","password":"unused"}'
        with patch('market.views.authenticate') as backend:
            response = client.post('/api/v1/auth/login', body, content_type='application/json')
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.data['code'], 'request_too_large')
        backend.assert_not_called()

    def test_oversized_urlencoded_form_is_rejected_before_login_authentication(self):
        client = APIClient(enforce_csrf_checks=True)
        client.get('/api/v1/auth/csrf')
        body = 'username=' + 'x' * (6 * 1024 * 1024) + '&password=unused'
        with patch('market.views.authenticate') as backend:
            response = client.post('/api/v1/auth/login', body, content_type='application/x-www-form-urlencoded',
                HTTP_X_CSRFTOKEN=client.cookies['csrftoken'].value)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.data['code'], 'request_too_large')
        backend.assert_not_called()

    @override_settings(IMAGE_UPLOAD_MAX_BYTES=10)
    def test_stream_is_interrupted_before_oversized_chunk_reaches_storage(self):
        request = RequestFactory().post('/')
        handler = BoundedImageUploadHandler(request)
        self.assertEqual(handler.receive_data_chunk(b'1234567890', 0), b'1234567890')
        with self.assertRaises(StopUpload):
            handler.receive_data_chunk(b'1', 10)
        self.assertEqual(request._image_upload_error, 'image_too_large')

    @override_settings(IMAGE_UPLOAD_MAX_BYTES=128, PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
    def test_rejected_stream_never_reaches_image_decoder_or_storage(self):
        _, _, vendor, stall, _ = fixtures()
        client = APIClient()
        client.force_authenticate(vendor)
        with patch('market.merchant.Image.open') as decoder, patch('market.merchant.default_storage.save') as store:
            result = client.post(f'/api/v1/merchant/stalls/{stall.pk}/image',
                {'file': SimpleUploadedFile('large.png', b'x' * 129, content_type='image/png')}, format='multipart')
        self.assertEqual(result.status_code, 400)
        self.assertEqual(result.data['code'], 'image_too_large')
        decoder.assert_not_called()
        store.assert_not_called()


@override_settings(AUTH_FAILURE_WINDOW_SECONDS=900)
class AuthenticationCleanupTests(TestCase):
    def bucket(self, key, *, updated_hours=25, window_hours=25):
        now = timezone.now()
        return AuthenticationFailureBucket.objects.create(key=key, failures=99,
            updated_at=now-timedelta(hours=updated_hours), window_started_at=now-timedelta(hours=window_hours))

    def test_only_expired_buckets_inactive_for_24_hours_are_removed(self):
        self.bucket('expired')
        self.bucket('recently_used', updated_hours=1)
        self.bucket('current_window', window_hours=0)
        self.assertEqual(cleanup_authentication_buckets(), 1)
        self.assertEqual(set(AuthenticationFailureBucket.objects.values_list('key', flat=True)),
            {'recently_used', 'current_window'})

    @override_settings(AUTH_FAILURE_WINDOW_SECONDS=48*3600)
    def test_long_configured_window_remains_protected(self):
        self.bucket('still_locked', updated_hours=30, window_hours=30)
        self.assertEqual(cleanup_authentication_buckets(), 0)
        self.assertTrue(AuthenticationFailureBucket.objects.filter(pk='still_locked').exists())

    def test_cleanup_is_bounded_and_catches_up_across_batches(self):
        for key in ['one', 'two', 'three']:
            self.bucket(key)
        self.assertEqual(cleanup_authentication_buckets(limit=2), 2)
        self.assertEqual(AuthenticationFailureBucket.objects.count(), 1)
        self.assertEqual(cleanup_authentication_buckets(limit=2), 1)
        self.assertEqual(cleanup_authentication_buckets(limit=2), 0)


@skipUnless(connection.vendor == 'postgresql', 'Row-lock races require PostgreSQL.')
@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    AUTH_FAILURE_ACCOUNT_LIMIT=3, AUTH_FAILURE_IP_LIMIT=10, AUTH_FAILURE_WINDOW_SECONDS=900)
class SecurityConcurrencyTests(TransactionTestCase):
    def test_cleanup_skips_bucket_locked_by_an_in_flight_login(self):
        past = timezone.now() - timedelta(hours=25)
        AuthenticationFailureBucket.objects.create(key='locked_login', window_started_at=past, updated_at=past)
        locked, release = ThreadEvent(), ThreadEvent()
        def hold_lock():
            close_old_connections()
            try:
                with transaction.atomic():
                    AuthenticationFailureBucket.objects.select_for_update().get(pk='locked_login')
                    locked.set()
                    if not release.wait(timeout=10):
                        raise AssertionError('cleanup did not complete while a login held its lock')
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(hold_lock)
            try:
                self.assertTrue(locked.wait(timeout=5))
                self.assertEqual(cleanup_authentication_buckets(), 0)
                self.assertTrue(AuthenticationFailureBucket.objects.filter(pk='locked_login').exists())
            finally:
                release.set()
            future.result(timeout=5)
        self.assertEqual(cleanup_authentication_buckets(), 1)

    def test_simultaneous_bad_logins_do_not_lose_or_exceed_admitted_failures(self):
        barrier = Barrier(8)
        def attempt(_):
            close_old_connections()
            try:
                request = RequestFactory().post('/', REMOTE_ADDR='198.51.100.8')
                barrier.wait(timeout=10)
                try:
                    login_attempt(request, 'same_account', lambda: None)
                    return 'failed'
                except LoginRateLimited:
                    return 'limited'
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(attempt, range(8)))
        self.assertEqual(results.count('failed'), 3)
        self.assertEqual(results.count('limited'), 5)
        self.assertEqual(sorted(AuthenticationFailureBucket.objects.values_list('failures', flat=True)), [3, 3])

    def test_simultaneous_creation_retries_return_the_same_product(self):
        _, _, vendor, stall, _ = fixtures()
        barrier = Barrier(2)
        def create(_):
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(User.objects.get(pk=vendor.pk))
                barrier.wait(timeout=10)
                response = client.post(f'/api/v1/merchant/stalls/{stall.pk}/products',
                    {'name': 'concurrent-new-product', 'price_cents': 400,
                     'idempotency_key': 'concurrent-creation-key'}, format='json')
                return response.status_code, response.data.get('id')
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, range(2)))
        self.assertEqual(sorted(result[0] for result in results), [200, 201])
        self.assertEqual(len({result[1] for result in results}), 1)
        self.assertEqual(ProductCreation.objects.count(), 1)
