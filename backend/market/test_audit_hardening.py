from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase, RequestFactory, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .auth_limits import login_attempt, LoginRateLimited, _key, clear_account_failures
from .errors import BusinessError
from .media_policy import public_image
from .models import AuditLog, BusinessSession, Order, PaymentAttempt, PaymentRefund, Product, Stall, StallLocation
from .security_models import AuthenticationFailureBucket, MerchantOrderOperation
from .services import create_order, cancel_order, merchant_action
from .tests import fixtures, payload


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ReservationBudgetTests(TestCase):
    def setUp(self):
        self.student, self.other, self.vendor, self.stall, self.product = fixtures()
        self.product.stock = 100
        self.product.save()

    def extra_stall(self, index):
        stall = Stall.objects.create(merchant=self.stall.merchant, area=self.stall.area,
            name=f'独立摊位{index}', transaction_enabled=True)
        StallLocation.objects.create(stall=stall, address='南门', latitude=31.23, longitude=121.47)
        stall.current_session = BusinessSession.objects.create(stall=stall, status='open', closes_at=timezone.now()+timedelta(hours=2))
        stall.save()
        return stall, Product.objects.create(stall=stall, name='面', price_cents=1000, stock=30)

    def test_unique_keys_cannot_hoard_and_original_retry_is_free(self):
        data = payload(self.stall, self.product)
        first, _ = create_order(self.student, data)
        with self.assertRaises(BusinessError) as caught:
            create_order(self.student, payload(self.stall, self.product))
        self.assertEqual(caught.exception.detail['code'], 'stall_reservation_limit')
        self.assertEqual(caught.exception.detail['order_ids'], [str(first.pk)])
        replay, created = create_order(self.student, data)
        self.assertFalse(created)
        self.assertEqual(replay.pk, first.pk)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 99)

    def test_total_budget_and_cross_stall_expiration(self):
        first, _ = create_order(self.student, payload(self.stall, self.product))
        for index in range(2):
            stall, product = self.extra_stall(index)
            create_order(self.student, payload(stall, product))
        stall, product = self.extra_stall(3)
        with self.assertRaises(BusinessError) as caught:
            create_order(self.student, payload(stall, product))
        self.assertEqual(caught.exception.detail['code'], 'active_reservation_limit')
        Order.objects.filter(pk=first.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
        create_order(self.student, payload(stall, product))
        first.refresh_from_db()
        self.assertTrue(first.inventory_released)

    def test_quantity_total_and_historical_large_replay(self):
        data = payload(self.stall, self.product, 11)
        with self.assertRaises(BusinessError) as caught: create_order(self.student, data)
        self.assertEqual(caught.exception.detail['code'], 'order_quantity_limit')
        with override_settings(CHECKOUT_MAX_PORTIONS=20):
            old, _ = create_order(self.student, data)
        self.assertEqual(create_order(self.student, data)[0].pk, old.pk)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 89)

    def test_cancelled_orders_count_towards_frequency(self):
        for _ in range(6):
            order, _ = create_order(self.student, payload(self.stall, self.product))
            cancel_order(order.pk, self.student, '测试取消')
        with self.assertRaises(BusinessError) as caught: create_order(self.student, payload(self.stall, self.product))
        self.assertEqual(caught.exception.status_code, 429)
        self.assertEqual(caught.exception.detail['code'], 'checkout_rate_limited')
        self.assertGreater(caught.exception.detail['retry_after'], 0)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 100)

    def test_uncertain_expired_order_does_not_release_budget(self):
        order, _ = create_order(self.student, payload(self.stall, self.product))
        Order.objects.filter(pk=order.pk).update(expires_at=timezone.now()-timedelta(seconds=1), payment_review_required=True)
        with self.assertRaises(BusinessError) as caught: create_order(self.student, payload(self.stall, self.product))
        self.assertEqual(caught.exception.detail['code'], 'stall_reservation_limit')
        order.refresh_from_db()
        self.assertFalse(order.inventory_released)

    def test_normal_cancellation_and_paid_completion_release_budget(self):
        cancelled, _ = create_order(self.student, payload(self.stall, self.product))
        cancel_order(cancelled.pk, self.student, '正常取消')
        completed, _ = create_order(self.student, payload(self.stall, self.product))
        for action in ('accept', 'ready', 'confirm_payment', 'complete'):
            merchant_action(completed.pk, self.vendor, action, code=completed.pickup_code)
        next_order, created = create_order(self.student, payload(self.stall, self.product))
        self.assertTrue(created)
        self.assertEqual(next_order.status, 'pending')
        completed.refresh_from_db()
        self.assertEqual((completed.status, completed.payment_status), ('completed', 'paid'))

    def test_terminal_financial_holds_keep_budget_independent_of_inventory(self):
        from .financial import hold_code
        from .reservation_limits import enforce_reservation_limits
        order, _ = create_order(self.student, payload(self.stall, self.product))
        cancel_order(order.pk, self.student, '先退回库存')
        payment = PaymentAttempt.objects.create(order=order, merchant=self.stall.merchant,
            account_key='fixture-account', mchid='1234567890', appid='wxFixture',
            out_trade_no='reservation-fixture', channel='native', amount_cents=order.total_cents,
            expires_at=order.expires_at, status='paid')
        refund = PaymentRefund.objects.create(payment=payment, order=order,
            out_refund_no='reservation-refund', amount_cents=order.total_cents,
            reason='fixture', status='closed', resolved_at=timezone.now())
        for terminal in ('cancelled', 'rejected', 'completed'):
            for released in (False, True):
                for signal in ('review', 'refunding', 'active_payment', 'unresolved_refund', 'settled'):
                    with self.subTest(terminal=terminal, inventory_released=released, signal=signal):
                        Order.objects.filter(pk=order.pk).update(status=terminal,
                            inventory_released=released, payment_review_required=signal == 'review',
                            payment_status='refunding' if signal == 'refunding' else 'paid')
                        PaymentAttempt.objects.filter(pk=payment.pk).update(
                            status='reconcile' if signal == 'active_payment' else 'paid')
                        PaymentRefund.objects.filter(pk=refund.pk).update(
                            resolved_at=None if signal == 'unresolved_refund' else timezone.now())
                        order.refresh_from_db()
                        self.assertEqual(bool(hold_code(order)), signal != 'settled')
                        if signal == 'settled':
                            enforce_reservation_limits(self.student, self.stall, payload(self.stall, self.product)['items'])
                        else:
                            with self.assertRaises(BusinessError) as caught:
                                create_order(self.student, payload(self.stall, self.product))
                            self.assertEqual(caught.exception.detail['code'], 'stall_reservation_limit')
                            self.assertEqual(caught.exception.detail['order_ids'], [str(order.pk)])
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 100)


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class MerchantOperationReplayTests(TestCase):
    def setUp(self):
        self.student, self.other, self.vendor, self.stall, self.product = fixtures()
        self.order, _ = create_order(self.student, payload(self.stall, self.product))

    def test_entire_pickup_flow_replays_without_duplicate_audit(self):
        for action in ('accept', 'ready', 'confirm_payment', 'complete'):
            key = 'operation-'+action
            code = self.order.pickup_code if action == 'complete' else ''
            first = merchant_action(self.order.pk, self.vendor, action, code=code, idempotency_key=key)
            second = merchant_action(self.order.pk, self.vendor, action, code=code, idempotency_key=key)
            self.assertEqual(first.status, second.status)
            self.assertEqual(AuditLog.objects.filter(action='order_'+action, target=str(self.order.pk)).count(), 1)
        self.assertEqual(MerchantOrderOperation.objects.count(), 4)

    def test_identity_and_fingerprint_checked_before_replay(self):
        merchant_action(self.order.pk, self.vendor, 'accept', idempotency_key='same-request')
        with self.assertRaises(BusinessError) as caught:
            merchant_action(self.order.pk, self.vendor, 'ready', idempotency_key='same-request')
        self.assertEqual(caught.exception.detail['code'], 'idempotency_conflict')
        with self.assertRaises(BusinessError) as caught:
            merchant_action(self.order.pk, self.other, 'accept', idempotency_key='same-request')
        self.assertEqual(caught.exception.status_code, 404)

    def test_legacy_preparation_key_cannot_be_reused_for_a_different_action(self):
        merchant_action(self.order.pk, self.vendor, 'accept', idempotency_key='legacy-prep-key')
        # A pre-migration record has PreparationRequest but no generic operation row.
        MerchantOrderOperation.objects.filter(order=self.order).delete()
        replay = merchant_action(self.order.pk, self.vendor, 'accept', idempotency_key='legacy-prep-key')
        self.assertEqual(replay.status, 'preparing')
        with self.assertRaises(BusinessError) as caught:
            merchant_action(self.order.pk, self.vendor, 'ready', idempotency_key='legacy-prep-key')
        self.assertEqual(caught.exception.detail['code'], 'idempotency_conflict')


@override_settings(AUTH_FAILURE_ACCOUNT_LIMIT=3, AUTH_FAILURE_IP_LIMIT=60)
class ShortLoginCooldownTests(TestCase):
    def test_cross_ip_cooldown_does_not_extend_on_rejection(self):
        req = RequestFactory().post('/', REMOTE_ADDR='192.0.2.1')
        now = timezone.now()
        with patch('market.auth_limits.timezone.now', return_value=now):
            for _ in range(3): login_attempt(req, 'victim', lambda: None)
        account = AuthenticationFailureBucket.objects.get(pk=_key('account', 'victim'))
        req.META['REMOTE_ADDR'] = '192.0.2.2'
        with patch('market.auth_limits.timezone.now', return_value=now+timedelta(seconds=4)):
            with self.assertRaises(LoginRateLimited): login_attempt(req, 'victim', lambda: True)
        account.refresh_from_db()
        self.assertEqual(account.updated_at, now)
        with patch('market.auth_limits.timezone.now', return_value=now+timedelta(seconds=5)):
            self.assertTrue(login_attempt(req, 'victim', lambda: True))
        account.refresh_from_db()
        self.assertEqual(account.failures, 0)
        self.assertEqual(AuthenticationFailureBucket.objects.get(pk=_key('ip', '192.0.2.1')).failures, 3)

    def test_recovery_clear_preserves_ip_budget(self):
        req = RequestFactory().post('/', REMOTE_ADDR='192.0.2.1')
        login_attempt(req, 'victim', lambda: None)
        clear_account_failures('victim')
        self.assertEqual(AuthenticationFailureBucket.objects.get(pk=_key('account', 'victim')).failures, 0)
        self.assertEqual(AuthenticationFailureBucket.objects.get(pk=_key('ip', '192.0.2.1')).failures, 1)


class PublicBoundaryTests(TestCase):
    def test_default_is_private_and_public_entry_points_remain_available(self):
        cache.clear()
        self.assertEqual(settings.REST_FRAMEWORK['DEFAULT_PERMISSION_CLASSES'], ['rest_framework.permissions.IsAuthenticated'])
        api = APIClient()
        for url in ('/api/v1/config', '/api/v1/auth/me', '/api/v1/auth/csrf', '/api/v1/health'):
            self.assertEqual(api.get(url).status_code, 200, url)
        self.assertEqual(api.get('/api/v1/orders').status_code, 403)

    def test_owned_image_path_is_not_an_open_redirect_or_traversal(self):
        self.assertEqual(public_image('/media/menu/photo.webp'), '/media/menu/photo.webp')
        for value in ('https://tracking.example/image.png', '//tracking.example/a', '/media/../secret',
                      '/images/%2e%2e/secret', '/images/%255csecret', '/media/x?url=https://evil.example', '/media/\nimage'):
            self.assertEqual(public_image(value), '', value)


@skipUnless(connection.vendor == 'postgresql', 'Row locking requires PostgreSQL')
@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ReservationContentionTests(TransactionTestCase):
    def test_thousand_unique_requests_respect_single_reservation(self):
        student, _, _, stall, product = fixtures()
        Product.objects.filter(pk=product.pk).update(stock=20)
        def submit(index):
            close_old_connections()
            try:
                create_order(student, payload(stall, product, key=f'contention-{index}'))
                return True
            except BusinessError as exc:
                if exc.detail['code'] != 'stall_reservation_limit': raise
                return False
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=12) as pool:
            accepted = sum(pool.map(submit, range(1000)))
        self.assertEqual(accepted, 1)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(Product.objects.get(pk=product.pk).stock, 19)
