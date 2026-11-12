"""Exercise the production database cache, not an in-process cache substitute."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, timezone as datetime_timezone
from threading import Barrier
from unittest import skipUnless

from django.core.cache import cache
from django.core.management import call_command
from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .errors import BusinessError
from .models import Order
from .services import create_order, merchant_action
from .tests import fixtures, payload


@override_settings(
    DEMO_MODE=True, SERVICES_SIMULATION_ENABLED=False,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.db.DatabaseCache',
                        'LOCATION': 'pickup_attempt_test_cache'}},
)
class PickupCodeDatabaseCacheTests(TransactionTestCase):
    def setUp(self):
        call_command('createcachetable', verbosity=0)
        cache.clear()
        self.student, self.other, self.vendor, self.stall, self.product = fixtures()
        self.order, _ = create_order(self.student, payload(self.stall, self.product))
        for action in ('accept', 'ready', 'confirm_payment'):
            self.order = merchant_action(self.order.pk, self.vendor, action)
        self.key = f'pickup_code_fails:{self.order.pk}'
        self.wrong = '0' * len(self.order.pickup_code)
        if self.wrong == self.order.pickup_code:
            self.wrong = '1' * len(self.order.pickup_code)
        self.api = APIClient()
        self.api.force_authenticate(self.vendor)

    def complete(self, code=None, **fields):
        return self.api.post(f'/api/v1/merchant/orders/{self.order.pk}/action',
            {'action': 'complete', 'pickup_code': self.wrong if code is None else code, **fields}, format='json')

    def test_five_errors_commit_and_later_attempts_are_locked(self):
        for attempt in range(1, 6):
            response = self.complete()
            self.assertEqual((response.status_code, response.data['code']), (400, 'invalid_pickup_code'))
            self.assertEqual(cache.get(self.key), attempt)
        for code in (self.wrong, self.order.pickup_code):
            response = self.complete(code)
            self.assertEqual((response.status_code, response.data['code']), (409, 'pickup_code_locked'))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'ready')
        self.assertIsNone(self.order.completed_at)

    def test_success_clears_attempts_and_same_operation_replays(self):
        self.complete()
        response = self.complete(self.order.pickup_code, idempotency_key='confirm-once')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], 'completed')
        self.assertIsNone(cache.get(self.key))
        self.assertEqual(self.complete(self.order.pickup_code, idempotency_key='confirm-once').status_code, 200)

    def test_unauthorized_user_cannot_consume_another_order_attempts(self):
        self.api.force_authenticate(self.other)
        self.assertEqual(self.complete().status_code, 404)
        self.assertIsNone(cache.get(self.key))

    def test_unpaid_order_cannot_be_completed_or_consume_attempts(self):
        Order.objects.filter(pk=self.order.pk).update(payment_status='unpaid', paid_at=None)
        response = self.complete()
        self.assertEqual(response.data['code'], 'invalid_transition')
        self.assertIsNone(cache.get(self.key))

    def test_database_expiry_is_ten_minutes_and_expired_lock_allows_correct_code(self):
        for _ in range(5):
            self.complete()
        with connection.cursor() as cursor:
            cursor.execute('SELECT expires FROM pickup_attempt_test_cache WHERE cache_key = %s', [cache.make_key(self.key)])
            expires = cursor.fetchone()[0]
            # SQLite returns naive UTC; PostgreSQL uses the connection timezone.
            if timezone.is_naive(expires):
                expires = timezone.make_aware(expires, datetime_timezone.utc)
            remaining = (expires - timezone.now()).total_seconds()
            self.assertGreater(remaining, 590)
            self.assertLessEqual(remaining, 600)
            cursor.execute('UPDATE pickup_attempt_test_cache SET expires = %s WHERE cache_key = %s',
                [connection.ops.adapt_datetimefield_value(timezone.now() - timedelta(seconds=1)), cache.make_key(self.key)])
        self.assertEqual(self.complete(self.order.pickup_code).status_code, 200)

    @skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL row locks')
    def test_parallel_failures_share_the_fifth_attempt(self):
        cache.set(self.key, 4, 600)
        barrier = Barrier(2)

        def attempt():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:
                    merchant_action(self.order.pk, self.vendor, 'complete', self.wrong)
                except BusinessError as error:
                    return error.detail['code']
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: attempt(), range(2)))
        self.assertCountEqual(results, ['invalid_pickup_code', 'pickup_code_locked'])
        self.assertEqual(cache.get(self.key), 5)
