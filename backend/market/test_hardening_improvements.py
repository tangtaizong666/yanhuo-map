"""Regression tests for financial, security, capacity, media, and pickup hardening."""
import io
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import skipUnless
from unittest.mock import Mock, patch

from PIL import Image
from django.conf import settings
from django.contrib.auth.models import Permission, User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, close_old_connections, connection, connections, transaction
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .auth_limits import client_ip_bucket
from .delivery import delivery_settings
from .errors import BusinessError
from .models import (
    OPEN_ORDER_STATUSES,
    FinancialEvidence,
    MerchantProfile,
    Order,
    PaymentAttempt,
    Stall,
    pickup_code,
)
from .proxy import daily_quota_exhausted
from .security_models import DailyQuotaCounter
from .payments import close_payment, start_payment, sync_payment
from .reconciliation import confirm_uncreated_payment
from .services import create_order, merchant_action
from .test_delivery import DeliverySetup
from .test_payments import PaymentSetup, payment_result
from .tests import fixtures, payload
from .wechatpay import GatewayError


class PaymentAndReconciliationHardeningTests(PaymentSetup, TestCase):
    def setUp(self):
        super().setUp()
        self.operator = User.objects.create_user(
            'hardening-operator', password='OperatorPass123!', is_staff=True
        )
        self.operator.user_permissions.add(
            Permission.objects.get(codename='resolve_payments')
        )

    def test_ordernotexist_code_variant_recovers_creating_intent(self):
        creating_attempt = PaymentAttempt.objects.create(
            order=self.order,
            merchant=self.stall.merchant,
            account_key='own-account',
            mchid='1234567890',
            appid='wxTestApp',
            out_trade_no=uuid.uuid4().hex,
            channel='native',
            amount_cents=self.order.total_cents,
            status='creating',
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        self.gateway.query_payment.side_effect = GatewayError(
            'ORDERNOTEXIST', '订单不存在'
        )
        start_payment(self.order.pk, self.student, 'native', '127.0.0.1')
        creating_attempt.refresh_from_db()
        self.assertEqual(creating_attempt.status, 'pending')
        self.assertEqual(self.gateway.create_payment.call_count, 1)

    def test_payerror_and_revoked_states_reconcile_without_invalid_state_hold(self):
        # REVOKED closes the attempt cleanly just like CLOSED
        revoked = PaymentAttempt.objects.create(
            order=self.order,
            merchant=self.stall.merchant,
            account_key='own-account',
            mchid='1234567890',
            appid='wxTestApp',
            out_trade_no=uuid.uuid4().hex,
            channel='native',
            amount_cents=self.order.total_cents,
            status='pending',
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        self.gateway.query_payment.side_effect = lambda num: payment_result(
            revoked, 'REVOKED'
        )
        sync_payment(self.order.pk)
        revoked.refresh_from_db()
        self.assertEqual(revoked.status, 'closed')
        self.assertEqual(revoked.error_code, '')

        # PAYERROR syncs cleanly without raising INVALID_PAYMENT_STATE, and close_payment closes it at provider
        payerr = PaymentAttempt.objects.create(
            order=self.order,
            merchant=self.stall.merchant,
            account_key='own-account',
            mchid='1234567890',
            appid='wxTestApp',
            out_trade_no=uuid.uuid4().hex,
            channel='native',
            amount_cents=self.order.total_cents,
            status='creating',
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        self.gateway.query_payment.side_effect = lambda num: payment_result(
            payerr, 'PAYERROR'
        )
        sync_payment(self.order.pk)
        payerr.refresh_from_db()
        self.assertEqual(payerr.status, 'pending')
        self.assertEqual(payerr.error_code, '')

        close_payment(self.order.pk, self.student)
        payerr.refresh_from_db()
        self.assertEqual(payerr.status, 'closed')
        self.gateway.close_payment.assert_called_with(payerr.out_trade_no)

    def test_confirm_uncreated_payment_requires_five_minute_grace_and_live_verification(self):
        attempt = PaymentAttempt.objects.create(
            order=self.order,
            merchant=self.stall.merchant,
            account_key='own-account',
            mchid='1234567890',
            appid='wxTestApp',
            out_trade_no=uuid.uuid4().hex,
            channel='native',
            amount_cents=self.order.total_cents,
            status='creating',
            expires_at=timezone.now() - timedelta(minutes=2),
        )
        with self.assertRaises(BusinessError) as too_early:
            confirm_uncreated_payment(
                self.order.pk, attempt.pk, self.operator, '核验过早测试原因说明'
            )
        self.assertEqual(
            too_early.exception.detail['code'], 'payment_not_expired_long_enough'
        )

        attempt.expires_at = timezone.now() - timedelta(minutes=6)
        attempt.save(update_fields=['expires_at'])
        self.gateway.query_payment.side_effect = GatewayError(
            'ORDERNOTEXIST', '服务商未创单'
        )
        resolved_order = confirm_uncreated_payment(
            self.order.pk, attempt.pk, self.operator, '已满5分钟向微信核验未创单'
        )
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, 'closed')
        self.assertEqual(resolved_order.payment_status, 'unpaid')
        self.assertTrue(
            FinancialEvidence.objects.filter(
                order=self.order,
                payment=attempt,
                operation='uncreated_payment_closed',
                outcome='resolved',
            ).exists()
        )


class DeliveryPaymentGraceTests(DeliverySetup, TestCase):
    def test_delivery_payment_confirmation_grace_and_eta_recomputation(self):
        base_now = timezone.now().replace(second=10, microsecond=0)
        local_base = timezone.localtime(base_now)
        window_start = (local_base - timedelta(hours=2)).time().replace(microsecond=0)
        window_end = (local_base + timedelta(minutes=2)).time().replace(microsecond=0)
        self.stall.delivery_starts_at = window_start
        self.stall.delivery_ends_at = window_end
        self.stall.save(update_fields=['delivery_starts_at', 'delivery_ends_at'])

        with patch('django.utils.timezone.now', return_value=base_now):
            order = self.create()
            start_payment(order.pk, self.student, 'native', '127.0.0.1')
        payment = PaymentAttempt.objects.get(order=order)

        # Paid 6 minutes after window end (+8 min after order creation, within 16-min grace) -> accepted into pending!
        paid_within_grace = base_now + timedelta(minutes=8)
        with patch('django.utils.timezone.now', return_value=paid_within_grace):
            self.notify(payment, success_time=paid_within_grace.isoformat())
        order.refresh_from_db()
        self.assertEqual(order.status, 'pending')
        self.assertGreaterEqual(
            order.delivery_eta_min_at,
            paid_within_grace + timedelta(minutes=self.stall.delivery_eta_min_minutes - 1),
        )

        # Second order paid 18 minutes after creation (> 16-min window grace, with extended attempt expiry) -> auto-cancelled & refund queued!
        with patch('django.utils.timezone.now', return_value=base_now):
            late_order = self.create(idempotency_key=uuid.uuid4().hex)
            start_payment(late_order.pk, self.student, 'native', '127.0.0.1')
        late_payment = PaymentAttempt.objects.get(order=late_order)
        PaymentAttempt.objects.filter(pk=late_payment.pk).update(
            expires_at=base_now + timedelta(minutes=25)
        )
        late_payment.refresh_from_db()
        paid_too_late = base_now + timedelta(minutes=18)
        with patch('django.utils.timezone.now', return_value=paid_too_late):
            self.notify(late_payment, success_time=paid_too_late.isoformat())
        late_order.refresh_from_db()
        self.assertEqual(late_order.status, 'cancelled')
        self.assertIn(late_order.payment_status, ('refunding', 'refunded'))


class MediaAndQrProtectionTests(TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        override = override_settings(MEDIA_ROOT=self.tmpdir.name, DEMO_MODE=False)
        override.enable()
        self.addCleanup(override.disable)
        cache.clear()
        self.student, self.other, self.vendor, self.stall, self.product = fixtures()
        other_user = User.objects.create_user('vendor2', password='Password123!')
        other_merchant = MerchantProfile.objects.create(
            user=other_user, business_name='二号摊', contact_phone='13900000002'
        )
        self.other_stall = Stall.objects.create(
            merchant=other_merchant,
            area=self.stall.area,
            name='隔壁烤冷面',
            category='小吃',
        )
        self.client = APIClient()

    def _png_bytes(self, rgba=(0, 0, 0, 0)):
        buf = io.BytesIO()
        img = Image.new('RGBA', (32, 32), rgba)
        img.save(buf, format='PNG')
        return buf.getvalue()

    def test_rgba_png_upload_composites_onto_white_background(self):
        self.client.force_authenticate(self.vendor)
        upload = SimpleUploadedFile(
            'transparent.png', self._png_bytes(), content_type='image/png'
        )
        res = self.client.post(
            f'/api/v1/merchant/stalls/{self.stall.pk}/image', {'file': upload}
        )
        self.assertEqual(res.status_code, 201, res.data)
        rel_path = res.data['url'].removeprefix(settings.MEDIA_URL)
        with Image.open(f'{self.tmpdir.name}/{rel_path}') as saved:
            r, g, b = saved.getpixel((16, 16))
            self.assertGreater(r, 250)
            self.assertGreater(g, 250)
            self.assertGreater(b, 250)

    def test_payment_qr_upload_access_control_and_replacement_cleanup(self):
        self.client.force_authenticate(self.vendor)
        qr1 = SimpleUploadedFile(
            'qr1.png', self._png_bytes((20, 20, 20, 255)), content_type='image/png'
        )
        up1 = self.client.post(
            f'/api/v1/merchant/stalls/{self.stall.pk}/image?purpose=payment_qr',
            {'file': qr1},
        )
        self.assertEqual(up1.status_code, 201, up1.data)
        qr1_url = up1.data['url']
        self.assertIn(f'/media/merchants/{self.stall.pk}/qr-', qr1_url)

        with self.captureOnCommitCallbacks(execute=True):
            patch_res = self.client.patch(
                f'/api/v1/merchant/stalls/{self.stall.pk}/profile',
                {'payment_qr_image': qr1_url},
                format='json',
            )
        self.assertEqual(patch_res.status_code, 200, patch_res.data)

        # Owning merchant can read protected QR with no-store & nosniff (via session cookie)
        owner_session = APIClient()
        owner_session.force_login(self.vendor)
        owner_read = owner_session.get(qr1_url)
        self.assertEqual(owner_read.status_code, 200)
        self.assertEqual(owner_read['Cache-Control'], 'private, no-store')
        self.assertEqual(owner_read['X-Content-Type-Options'], 'nosniff')
        list(owner_read.streaming_content)

        # Anonymous and unrelated student get non-cacheable 404
        anon = APIClient()
        anon_qr = anon.get(qr1_url)
        self.assertEqual(anon_qr.status_code, 404)
        self.assertEqual(anon_qr['Cache-Control'], 'private, no-store')
        other_client = APIClient()
        other_client.force_login(self.other)
        self.assertEqual(other_client.get(qr1_url).status_code, 404)

        # Ordering student gets 404 while pending/preparing, 200 once ready, and 404 once completed
        student_client = APIClient()
        student_client.force_login(self.student)
        order, _ = create_order(self.student, payload(self.stall, self.product))
        self.assertEqual(student_client.get(qr1_url).status_code, 404)
        merchant_action(order.pk, self.vendor, 'accept')
        self.assertEqual(student_client.get(qr1_url).status_code, 404)
        merchant_action(order.pk, self.vendor, 'ready')
        ready_read = student_client.get(qr1_url)
        self.assertEqual(ready_read.status_code, 200)
        list(ready_read.streaming_content)
        merchant_action(order.pk, self.vendor, 'confirm_payment')
        merchant_action(order.pk, self.vendor, 'complete', order.pickup_code)
        self.assertEqual(student_client.get(qr1_url).status_code, 404)

        # Replacing payment_qr_image deletes old qr1 file on commit
        qr2 = SimpleUploadedFile(
            'qr2.png', self._png_bytes((40, 40, 40, 255)), content_type='image/png'
        )
        up2 = self.client.post(
            f'/api/v1/merchant/stalls/{self.stall.pk}/image?purpose=payment_qr',
            {'file': qr2},
        )
        qr2_url = up2.data['url']
        with self.captureOnCommitCallbacks(execute=True):
            rep = self.client.patch(
                f'/api/v1/merchant/stalls/{self.stall.pk}/profile',
                {'payment_qr_image': qr2_url},
                format='json',
            )
        self.assertEqual(rep.status_code, 200, rep.data)
        self.assertEqual(owner_session.get(qr1_url).status_code, 404)
        qr2_read = owner_session.get(qr2_url)
        self.assertEqual(qr2_read.status_code, 200)
        list(qr2_read.streaming_content)

    def test_public_stall_and_product_fields_reject_qr_and_cross_stall_media(self):
        self.client.force_authenticate(self.vendor)
        for bad_url in (
            f'/media/merchants/{self.stall.pk}/qr-secret.jpg',
            f'/media/merchants/{self.other_stall.pk}/dish.jpg',
        ):
            r_stall = self.client.patch(
                f'/api/v1/merchant/stalls/{self.stall.pk}/profile',
                {'image': bad_url},
                format='json',
            )
            self.assertEqual(r_stall.status_code, 400)
            r_prod = self.client.patch(
                f'/api/v1/merchant/products/{self.product.pk}',
                {'image': bad_url},
                format='json',
            )
            self.assertEqual(r_prod.status_code, 400)

        # Curated shared preset path remains allowed
        ok_res = self.client.patch(
            f'/api/v1/merchant/stalls/{self.stall.pk}/profile',
            {'image': '/media/merchants/scene.jpg'},
            format='json',
        )
        self.assertEqual(ok_res.status_code, 200, ok_res.data)


class SecurityCapacityAndThrottleHardeningTests(DeliverySetup, TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()

    def test_cache_max_entries_ipv6_bucket_and_private_cache_control(self):
        self.assertGreaterEqual(
            settings.CACHES['default'].get('OPTIONS', {}).get('MAX_ENTRIES', 0),
            100000,
        )
        rf = RequestFactory()
        r1 = rf.get('/', REMOTE_ADDR='2001:db8:abcd:1234::1')
        r2 = rf.get('/', REMOTE_ADDR='2001:db8:abcd:1234:ffff:eeee:dddd:9999')
        r3 = rf.get('/', REMOTE_ADDR='2001:db8:abcd:5678::1')
        self.assertEqual(client_ip_bucket(r1, trust_proxy=False), '2001:db8:abcd:1234::/64')
        self.assertEqual(
            client_ip_bucket(r1, trust_proxy=False),
            client_ip_bucket(r2, trust_proxy=False),
        )
        self.assertNotEqual(
            client_ip_bucket(r1, trust_proxy=False),
            client_ip_bucket(r3, trust_proxy=False),
        )

        orders_res = self.api.get('/api/v1/orders')
        self.assertEqual(orders_res.status_code, 200)
        self.assertEqual(orders_res['Cache-Control'], 'private, no-store')
        anon_cfg = APIClient().get('/api/v1/config')
        self.assertEqual(anon_cfg.status_code, 200)
        self.assertEqual(anon_cfg['Cache-Control'], 'private, no-store')

    @override_settings(
        AMAP_KEY='test-amap-key',
        AMAP_SECURITY_CODE='SecRetJsCode99',
        AMAP_PROXY_DAILY_LIMIT=10,
    )
    def test_amap_proxy_hardening_duplicate_keys_headers_and_secret_scrubbing(self):
        client = APIClient()
        base = '/api/v1/amap-proxy/_AMapService/v3/place/text'
        headers = {'HTTP_ORIGIN': 'http://testserver'}
        # Duplicate query parameter rejected
        dup = client.get(f'{base}?keywords=a&keywords=b', **headers)
        self.assertEqual(dup.status_code, 400)
        # Case-folded forbidden parameter rejected (`Url` / `jscode[]`)
        forb = client.get(f'{base}?Url=https://evil.example', **headers)
        self.assertEqual(forb.status_code, 400)

        fake_resp = Mock()
        fake_resp.status = 200
        fake_resp.headers = {'Content-Type': 'text/html; charset=utf-8'}
        fake_resp.read.return_value = b'{"status":"1"}'
        fake_cm = Mock(__enter__=Mock(return_value=fake_resp), __exit__=Mock(return_value=False))
        mock_opener = Mock()
        mock_opener.open.return_value = fake_cm
        with patch('market.proxy.urllib.request.build_opener', return_value=mock_opener):
            ok = client.get(f'{base}?keywords=noodles', **headers)
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok['X-Content-Type-Options'], 'nosniff')
        self.assertIn("default-src 'none'", ok['Content-Security-Policy'])
        self.assertNotIn('text/html', ok['Content-Type'])

        # Case-insensitive security code scrubbing on upstream response body
        fake_resp.read.return_value = b'{"error":"upstream leaked secretjscode99"}'
        with patch('market.proxy.urllib.request.build_opener', return_value=mock_opener):
            err = client.get(f'{base}?keywords=rice', **headers)
        self.assertEqual(err.status_code, 502)
        self.assertNotIn('secretjscode99', err.content.decode('utf-8').lower())

    def test_pickup_lookup_five_failure_lockout_and_strict_four_digit_exhaustion(self):
        pickup_order, _ = create_order(self.student, payload(self.stall, self.product))
        merchant_action(pickup_order.pk, self.vendor, 'accept')
        merchant_action(pickup_order.pk, self.vendor, 'ready')
        vendor_api = APIClient()
        vendor_api.force_authenticate(self.vendor)
        wrong_code = '0000' if pickup_order.pickup_code != '0000' else '9999'
        for _ in range(5):
            r = vendor_api.post(
                f'/api/v1/merchant/stalls/{self.stall.pk}/pickup-lookup',
                {'pickup_code': wrong_code, 'number': pickup_order.number},
                format='json',
            )
            self.assertEqual(r.status_code, 404)
        locked = vendor_api.post(
            f'/api/v1/merchant/stalls/{self.stall.pk}/pickup-lookup',
            {'pickup_code': pickup_order.pickup_code, 'number': pickup_order.number},
            format='json',
        )
        self.assertEqual(locked.status_code, 409)
        self.assertEqual(locked.data['code'], 'pickup_code_locked')

        all_four_digits = {f'{i:04d}' for i in range(10000)}
        with self.assertRaises(BusinessError) as raised:
            pickup_code(all_four_digits)
        self.assertEqual(raised.exception.detail['code'], 'pickup_code_exhausted')

    @override_settings(
        CHECKOUT_MAX_ACTIVE_PER_STALL=1,
        CHECKOUT_STALE_READY_HOURS=2,
    )
    def test_stale_arrived_delivery_exempt_from_capacity_and_reservation_limits(self):
        self.stall.delivery_capacity = 1
        self.stall.save(update_fields=['delivery_capacity'])
        first = self.create()
        self.to_arrived(first)
        # While fresh arrived, capacity is full (1/1)
        cap_fresh = delivery_settings(self.stall)
        self.assertFalse(cap_fresh['available'])

        # Once arrived > CHECKOUT_STALE_READY_HOURS ago, neither stall capacity nor student active limit is blocked
        stale_time = timezone.now() - timedelta(hours=3)
        Order.objects.filter(pk=first.pk).update(arrived_at=stale_time, created_at=stale_time)
        cap_stale = delivery_settings(self.stall)
        self.assertTrue(cap_stale['available'])
        second = self.create(idempotency_key=uuid.uuid4().hex)
        self.assertEqual(second.status, 'pending_payment')

    @override_settings(DEMO_MODE=False, AUTH_FAILURE_ACCOUNT_LIMIT=3)
    def test_delete_account_records_auth_failures_and_live_self_order_blocked(self):
        # Live self-order blocked
        with self.assertRaises(BusinessError) as self_err:
            create_order(self.vendor, payload(self.stall, self.product))
        self.assertEqual(self_err.exception.detail['code'], 'self_order_forbidden')

        # Delete account wrong password increments login_attempt lockout
        victim = User.objects.create_user('delete-lock-user', password='CorrectPassword123!')
        client = APIClient()
        client.force_authenticate(victim)
        statuses = []
        for _ in range(7):
            res = client.delete(
                '/api/v1/auth/account', {'password': 'WrongPassword!'}, format='json'
            )
            statuses.append(res.status_code)
        self.assertIn(429, statuses)

    @override_settings(AMAP_PROXY_DAILY_LIMIT=3)
    def test_amap_daily_quota_counter_enforces_exact_db_ceiling_and_prunes_stale_days(self):
        today = timezone.localdate()
        DailyQuotaCounter.objects.create(
            scope='amap_proxy', day=today - timedelta(days=95), count=12
        )
        outcomes = [daily_quota_exhausted() for _ in range(5)]
        self.assertEqual(outcomes, [False, False, False, True, True])
        row = DailyQuotaCounter.objects.get(scope='amap_proxy', day=today)
        self.assertEqual(row.count, 3)
        self.assertFalse(
            DailyQuotaCounter.objects.filter(day=today - timedelta(days=95)).exists()
        )
        with override_settings(AMAP_PROXY_DAILY_LIMIT=0):
            self.assertTrue(daily_quota_exhausted())

    def test_active_order_pickup_code_constraint_blocks_same_stall_duplicates_only_while_open(self):
        first, _ = create_order(self.student, payload(self.stall, self.product))
        second, _ = create_order(self.other, payload(self.stall, self.product))
        for active_state in OPEN_ORDER_STATUSES:
            Order.objects.filter(pk=first.pk).update(status=active_state)
            with self.assertRaises(IntegrityError), transaction.atomic():
                Order.objects.filter(pk=second.pk).update(pickup_code=first.pickup_code)

        # Another stall may independently hold the same active pickup code.
        other_merchant = MerchantProfile.objects.create(
            user=User.objects.create_user('other-stall-owner', password='Password123!'),
            business_name='另一商户',
            is_verified=True,
        )
        other_stall = Stall.objects.create(
            merchant=other_merchant, area=self.stall.area, name='另一摊', category='小吃'
        )
        foreign_order = Order.objects.create(
            user=self.student,
            stall=other_stall,
            stall_name=other_stall.name,
            status='ready',
            pickup_code=first.pickup_code,
            total_cents=1000,
            expires_at=timezone.now() + timedelta(minutes=10),
            pickup_address='北门',
            pickup_latitude=30.0,
            pickup_longitude=120.0,
            idempotency_key=uuid.uuid4().hex,
            request_hash='foreign-stall',
        )
        self.assertEqual(foreign_order.pickup_code, first.pickup_code)

        # Once terminal, the same stall can reuse that pickup code.
        for terminal_state in ('completed', 'cancelled', 'rejected'):
            Order.objects.filter(pk=first.pk).update(status=terminal_state)
            Order.objects.filter(pk=second.pk).update(
                status='ready', pickup_code=first.pickup_code
            )
            second.refresh_from_db()
            self.assertEqual(second.pickup_code, first.pickup_code)
            Order.objects.filter(pk=second.pk).update(pickup_code=pickup_code({first.pickup_code}))

    @override_settings(AMAP_KEY='test-key', AMAP_SECURITY_CODE='test-secret')
    def test_map_busy_does_not_deduct_daily_amap_quota(self):
        from . import proxy
        with patch.object(proxy._proxy_slots, 'acquire', return_value=False):
            res = self.client.get(
                '/api/v1/amap-proxy/_AMapService/v3/ip',
                HTTP_ORIGIN='http://testserver',
            )
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json()['code'], 'map_busy')
        self.assertEqual(DailyQuotaCounter.objects.count(), 0)

    def test_media_policy_rejects_percent_encoded_and_duplicate_slash_paths(self):
        from .media_policy import owned_media_path, protected_qr_image, public_image
        malformed = [
            f'/media/merchants/{self.stall.pk}/%71r-secret.jpg',
            f'/media/merchants/{self.stall.pk}/qr%2dsecret.jpg',
            f'/media/merchants//{self.stall.pk}/qr-secret.jpg',
            f'/media/merchants/{self.stall.pk}/QR-secret.jpg',
            '/images//food-burger.jpg',
            '/images/food%2dburger.jpg',
        ]
        for candidate in malformed:
            self.assertEqual(public_image(candidate), '', candidate)
            self.assertEqual(protected_qr_image(candidate, stall_id=self.stall.pk), '', candidate)
        self.assertEqual(owned_media_path('/images/food-burger.jpg'), '/images/food-burger.jpg')

    def test_qr_replacement_on_commit_preserves_reverted_active_qr_and_cleans_legacy_file(self):
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(MEDIA_ROOT=tmpdir):
            api = APIClient()
            api.force_authenticate(self.vendor)
            qr_a_rel = f'merchants/{self.stall.pk}/qr-alpha.jpg'
            qr_b_rel = f'merchants/{self.stall.pk}/qr-beta.jpg'
            legacy_rel = f'merchants/{self.stall.pk}/legacy-public.jpg'
            default_storage.save(qr_a_rel, ContentFile(b'alpha'))
            default_storage.save(qr_b_rel, ContentFile(b'beta'))
            default_storage.save(legacy_rel, ContentFile(b'legacy'))
            qr_a_url = f'{settings.MEDIA_URL}{qr_a_rel}'
            qr_b_url = f'{settings.MEDIA_URL}{qr_b_rel}'
            legacy_url = f'{settings.MEDIA_URL}{legacy_rel}'

            Stall.objects.filter(pk=self.stall.pk).update(payment_qr_image=legacy_url)
            # Legacy public file replaced by qr-alpha -> legacy file cleaned up on commit
            with self.captureOnCommitCallbacks(execute=True):
                r0 = api.patch(
                    f'/api/v1/merchant/stalls/{self.stall.pk}/profile',
                    {'payment_qr_image': qr_a_url},
                    format='json',
                )
            self.assertEqual(r0.status_code, 200, r0.data)
            self.assertFalse(default_storage.exists(legacy_rel))
            self.assertTrue(default_storage.exists(qr_a_rel))

            # Rapid A -> B -> A before on_commit executes must NOT delete active A
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                r1 = api.patch(
                    f'/api/v1/merchant/stalls/{self.stall.pk}/profile',
                    {'payment_qr_image': qr_b_url},
                    format='json',
                )
                self.assertEqual(r1.status_code, 200, r1.data)
                r2 = api.patch(
                    f'/api/v1/merchant/stalls/{self.stall.pk}/profile',
                    {'payment_qr_image': qr_a_url},
                    format='json',
                )
                self.assertEqual(r2.status_code, 200, r2.data)
            for cb in callbacks:
                cb()
            self.assertTrue(default_storage.exists(qr_a_rel))
            self.assertFalse(default_storage.exists(qr_b_rel))


class HistoricalMigrationUpgradeTests(TransactionTestCase):
    def test_0023_to_0025_dedupes_open_pickup_codes_and_relocates_legacy_qr(self):
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage
        from django.db.migrations.executor import MigrationExecutor

        migrate_from = [('market', '0023_auditlog_target_index')]
        migrate_to = [('market', '0025_migrate_legacy_public_payment_qr')]

        def restore_latest():
            exec_restore = MigrationExecutor(connection)
            exec_restore.migrate(exec_restore.loader.graph.leaf_nodes())

        self.addCleanup(restore_latest)

        with tempfile.TemporaryDirectory() as tmpdir, override_settings(MEDIA_ROOT=tmpdir):
            executor = MigrationExecutor(connection)
            executor.migrate(migrate_from)
            old_apps = executor.loader.project_state(migrate_from).apps
            HistUser = old_apps.get_model('auth', 'User')
            HistArea = old_apps.get_model('market', 'Area')
            HistMerchant = old_apps.get_model('market', 'MerchantProfile')
            HistStall = old_apps.get_model('market', 'Stall')
            HistOrder = old_apps.get_model('market', 'Order')

            u = HistUser.objects.create(username='hist-user')
            m_user = HistUser.objects.create(username='hist-vendor')
            area = HistArea.objects.create(name='旧区', latitude=30.0, longitude=120.0)
            mp = HistMerchant.objects.create(user=m_user, business_name='旧摊商户', is_verified=True)
            stall = HistStall.objects.create(merchant=mp, area=area, name='旧摊', category='小吃')
            legacy_rel = f'merchants/{stall.pk}/legacy-qr.jpg'
            default_storage.save(legacy_rel, ContentFile(b'legacy-qr-bytes'))
            HistStall.objects.filter(pk=stall.pk).update(payment_qr_image=f'/media/{legacy_rel}')

            now = timezone.now()
            common = dict(
                user=u,
                stall=stall,
                stall_name='旧摊',
                total_cents=800,
                expires_at=now + timedelta(minutes=15),
                pickup_address='南门',
                pickup_latitude=30.0,
                pickup_longitude=120.0,
            )
            open_a = HistOrder.objects.create(
                status='ready', pickup_code='4455', idempotency_key='k1', request_hash='h1', **common
            )
            open_b = HistOrder.objects.create(
                status='preparing', pickup_code='4455', idempotency_key='k2', request_hash='h2', **common
            )
            done_a = HistOrder.objects.create(
                status='completed', pickup_code='8899', idempotency_key='k3', request_hash='h3', **common
            )
            done_b = HistOrder.objects.create(
                status='completed', pickup_code='8899', idempotency_key='k4', request_hash='h4', **common
            )

            executor = MigrationExecutor(connection)
            executor.loader.build_graph()
            executor.migrate(migrate_to)

            new_apps = executor.loader.project_state(migrate_to).apps
            NewOrder = new_apps.get_model('market', 'Order')
            NewStall = new_apps.get_model('market', 'Stall')

            code_a = NewOrder.objects.get(pk=open_a.pk).pickup_code
            code_b = NewOrder.objects.get(pk=open_b.pk).pickup_code
            self.assertEqual(code_a, '4455')
            self.assertNotEqual(code_b, '4455')
            self.assertEqual(NewOrder.objects.get(pk=done_a.pk).pickup_code, '8899')
            self.assertEqual(NewOrder.objects.get(pk=done_b.pk).pickup_code, '8899')

            upgraded_stall = NewStall.objects.get(pk=stall.pk)
            expected_protected_rel = f'merchants/{stall.pk}/qr-legacy-qr.jpg'
            self.assertEqual(upgraded_stall.payment_qr_image, f'/media/{expected_protected_rel}')
            self.assertTrue(default_storage.exists(expected_protected_rel))
            self.assertFalse(default_storage.exists(legacy_rel))

    def test_0026_to_0027_normalizes_legacy_pickup_codes_to_four_digits(self):
        from django.db.migrations.executor import MigrationExecutor

        migrate_from = [('market', '0026_review_governance')]
        migrate_to = [('market', '0027_four_digit_pickup_code')]

        def restore_latest():
            exec_restore = MigrationExecutor(connection)
            exec_restore.migrate(exec_restore.loader.graph.leaf_nodes())

        self.addCleanup(restore_latest)

        executor = MigrationExecutor(connection)
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        HistUser = old_apps.get_model('auth', 'User')
        HistArea = old_apps.get_model('market', 'Area')
        HistMerchant = old_apps.get_model('market', 'MerchantProfile')
        HistStall = old_apps.get_model('market', 'Stall')
        HistOrder = old_apps.get_model('market', 'Order')

        u = HistUser.objects.create(username='hist27-user')
        m_user = HistUser.objects.create(username='hist27-vendor')
        area = HistArea.objects.create(name='南区旧点', latitude=45.71, longitude=126.63)
        mp = HistMerchant.objects.create(user=m_user, business_name='南区摊主', is_verified=True)
        stall = HistStall.objects.create(merchant=mp, area=area, name='南区摊位', category='小吃')
        now = timezone.now()
        common = dict(
            user=u,
            stall=stall,
            stall_name='南区摊位',
            total_cents=800,
            expires_at=now + timedelta(minutes=15),
            pickup_address='哈理工南区',
            pickup_latitude=45.711,
            pickup_longitude=126.634,
        )
        open_keep = HistOrder.objects.create(
            status='ready', pickup_code='8562', idempotency_key='k27-1', request_hash='h27-1', **common
        )
        open_legacy = HistOrder.objects.create(
            status='preparing', pickup_code='76358562', idempotency_key='k27-2', request_hash='h27-2', **common
        )
        closed_legacy = HistOrder.objects.create(
            status='completed', pickup_code='60659380', idempotency_key='k27-3', request_hash='h27-3', **common
        )

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(migrate_to)

        new_apps = executor.loader.project_state(migrate_to).apps
        NewOrder = new_apps.get_model('market', 'Order')
        code_keep = NewOrder.objects.get(pk=open_keep.pk).pickup_code
        code_legacy = NewOrder.objects.get(pk=open_legacy.pk).pickup_code
        code_closed = NewOrder.objects.get(pk=closed_legacy.pk).pickup_code
        self.assertEqual(code_keep, '8562')
        self.assertRegex(code_legacy, r'^[0-9]{4}$')
        self.assertNotEqual(code_legacy, '8562')
        self.assertEqual(code_closed, '9380')

    def test_0025_rollback_preserves_legacy_file_and_collision_writes_distinct_qr(self):
        import importlib
        from django.apps import apps as django_apps
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        mig0025 = importlib.import_module('market.migrations.0025_migrate_legacy_public_payment_qr')
        with tempfile.TemporaryDirectory() as tmpdir, override_settings(MEDIA_ROOT=tmpdir):
            _, _, _, stall, _ = fixtures()
            legacy_rel = f'merchants/{stall.pk}/rollback-qr.jpg'
            preexisting_qr_rel = f'merchants/{stall.pk}/qr-rollback-qr.jpg'
            default_storage.save(legacy_rel, ContentFile(b'real-legacy-qr'))
            default_storage.save(preexisting_qr_rel, ContentFile(b'other-preexisting-qr'))
            Stall.objects.filter(pk=stall.pk).update(payment_qr_image=f'/media/{legacy_rel}')

            # 1) Simulated mid-migration failure: DB rolls back and old public file is NOT deleted prematurely
            from types import SimpleNamespace
            fake_editor = SimpleNamespace(connection=connection)
            with self.assertRaises(RuntimeError), transaction.atomic():
                mig0025.migrate_legacy_public_payment_qr(django_apps, fake_editor)
                raise RuntimeError('simulated_migration_abort')

            stall.refresh_from_db()
            self.assertEqual(stall.payment_qr_image, f'/media/{legacy_rel}')
            self.assertTrue(default_storage.exists(legacy_rel))

            # 2) Successful commit when qr-rollback-qr.jpg already exists writes a distinct qr-* file with exact bytes
            with transaction.atomic():
                mig0025.migrate_legacy_public_payment_qr(django_apps, fake_editor)

            stall.refresh_from_db()
            self.assertNotEqual(stall.payment_qr_image, f'/media/{preexisting_qr_rel}')
            self.assertTrue(stall.payment_qr_image.startswith(f'/media/merchants/{stall.pk}/qr-'))
            saved_rel = stall.payment_qr_image.removeprefix('/media/')
            with default_storage.open(saved_rel, 'rb') as saved_fp:
                self.assertEqual(saved_fp.read(), b'real-legacy-qr')
            with default_storage.open(preexisting_qr_rel, 'rb') as pre_fp:
                self.assertEqual(pre_fp.read(), b'other-preexisting-qr')
            self.assertFalse(default_storage.exists(legacy_rel))


@skipUnless(connection.vendor == 'postgresql', 'PostgreSQL race test')
class DailyQuotaPostgreSQLRaceTests(TransactionTestCase):
    @override_settings(AMAP_PROXY_DAILY_LIMIT=3)
    def test_concurrent_cold_start_amap_quota_allows_exact_ceiling(self):
        gate = Barrier(8)

        def call_quota():
            close_old_connections()
            try:
                gate.wait(timeout=10)
                return daily_quota_exhausted()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(call_quota) for _ in range(8)]
            results = [future.result(timeout=30) for future in futures]

        self.assertEqual(results.count(False), 3)
        self.assertEqual(results.count(True), 5)
        counter = DailyQuotaCounter.objects.get(
            scope='amap_proxy', day=timezone.localdate()
        )
        self.assertEqual(counter.count, 3)

    def test_concurrent_qr_cleanup_and_profile_revert_never_leaves_dangling_file(self):
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage
        from .merchant import _cleanup_unreferenced_stall_media

        with tempfile.TemporaryDirectory() as tmpdir, override_settings(MEDIA_ROOT=tmpdir):
            _, _, vendor, stall, _ = fixtures()
            qr_a_rel = f'merchants/{stall.pk}/qr-race-a.jpg'
            qr_b_rel = f'merchants/{stall.pk}/qr-race-b.jpg'
            qr_a_url = f'{settings.MEDIA_URL}{qr_a_rel}'
            qr_b_url = f'{settings.MEDIA_URL}{qr_b_rel}'
            default_storage.save(qr_b_rel, ContentFile(b'beta'))

            for _ in range(8):
                if not default_storage.exists(qr_a_rel):
                    default_storage.save(qr_a_rel, ContentFile(b'alpha'))
                Stall.objects.filter(pk=stall.pk).update(payment_qr_image=qr_b_url)
                gate = Barrier(2)

                def run_cleanup():
                    close_old_connections()
                    try:
                        gate.wait(timeout=10)
                        _cleanup_unreferenced_stall_media(stall.pk, qr_a_url, qr_a_rel)
                    finally:
                        connections.close_all()

                def run_revert():
                    close_old_connections()
                    try:
                        api = APIClient()
                        api.force_authenticate(vendor)
                        gate.wait(timeout=10)
                        return api.patch(
                            f'/api/v1/merchant/stalls/{stall.pk}/profile',
                            {'payment_qr_image': qr_a_url},
                            format='json',
                        ).status_code
                    finally:
                        connections.close_all()

                with ThreadPoolExecutor(max_workers=2) as pool:
                    f_clean = pool.submit(run_cleanup)
                    f_revert = pool.submit(run_revert)
                    f_clean.result(timeout=30)
                    status_code = f_revert.result(timeout=30)

                stall.refresh_from_db()
                if status_code == 200:
                    self.assertEqual(stall.payment_qr_image, qr_a_url)
                    self.assertTrue(default_storage.exists(qr_a_rel))
                else:
                    self.assertEqual(status_code, 400)
                    self.assertEqual(stall.payment_qr_image, qr_b_url)
                    self.assertFalse(default_storage.exists(qr_a_rel))


class ComprehensiveAuditRegressionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.area, self.student, self.vendor, self.stall, self.product = fixtures()

    def test_serve_public_media_blocks_all_qr_path_variants_and_serves_normal_media(self):
        import os
        from pathlib import Path
        from config.urls import serve_public_media
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        rf = RequestFactory()
        with tempfile.TemporaryDirectory() as outer_dir, tempfile.TemporaryDirectory() as tmpdir, override_settings(MEDIA_ROOT=tmpdir):
            outside_secret = Path(outer_dir) / 'outside-secret.txt'
            outside_secret.write_bytes(b'outside-secret')
            default_storage.save(f'merchants/{self.stall.pk}/qr-secret.jpg', ContentFile(b'qr-secret'))
            default_storage.save(f'merchants/{self.stall.pk}/dish.jpg', ContentFile(b'dish-bytes'))

            symlink_rel = f'merchants/{self.stall.pk}/escaped-link.jpg'
            symlink_abs = Path(tmpdir) / symlink_rel
            symlink_created = False
            try:
                os.symlink(outside_secret, symlink_abs)
                symlink_created = True
            except (OSError, NotImplementedError):
                symlink_created = False

            blocked_paths = [
                f'merchants/{self.stall.pk}/qr-secret.jpg',
                f'merchants//{self.stall.pk}/qr-secret.jpg',
                f'merchants/{self.stall.pk}/./qr-secret.jpg',
                f'merchants/{self.stall.pk}/QR-SECRET.JPG',
                '../manage.py',
            ]
            if symlink_created:
                blocked_paths.append(symlink_rel)

            for blocked_path in blocked_paths:
                res = serve_public_media(rf.get(f'/media/{blocked_path}'), path=blocked_path)
                self.assertEqual(res.status_code, 404, blocked_path)
                self.assertEqual(res.headers.get('Cache-Control'), 'private, no-store')

            # Even on Windows without SeCreateSymbolicLinkPrivilege, verify is_symlink() / outside-resolve branches directly
            with patch.object(Path, 'is_symlink', return_value=True):
                sym_res = serve_public_media(
                    rf.get(f'/media/merchants/{self.stall.pk}/dish.jpg'),
                    path=f'merchants/{self.stall.pk}/dish.jpg',
                )
                self.assertEqual(sym_res.status_code, 404)
                self.assertEqual(sym_res.headers.get('Cache-Control'), 'private, no-store')

            real_media_root = Path(tmpdir).resolve()
            real_outside = outside_secret.resolve()
            with patch.object(Path, 'resolve', side_effect=[real_media_root, real_outside]):
                esc_res = serve_public_media(
                    rf.get(f'/media/merchants/{self.stall.pk}/dish.jpg'),
                    path=f'merchants/{self.stall.pk}/dish.jpg',
                )
                self.assertEqual(esc_res.status_code, 404)
                self.assertEqual(esc_res.headers.get('Cache-Control'), 'private, no-store')

            ok = serve_public_media(
                rf.get(f'/media/merchants/{self.stall.pk}/dish.jpg'),
                path=f'merchants/{self.stall.pk}/dish.jpg',
            )
            try:
                self.assertEqual(ok.status_code, 200)
                self.assertEqual(b''.join(ok.streaming_content), b'dish-bytes')
            finally:
                ok.close()

    def test_expire_orders_handles_missing_session_and_idempotent_stale_event(self):
        from django.core.management import call_command
        from .models import BusinessSession, Event, SiteConfiguration

        session = self.stall.current_session
        self.assertIsNotNone(session)
        config = SiteConfiguration.current()
        stale_time = timezone.now() - timedelta(minutes=config.stale_minutes + 15)
        BusinessSession.objects.filter(pk=session.pk).update(status='open', last_confirmed_at=stale_time)
        call_command('expire_orders')
        call_command('expire_orders')
        events = Event.objects.filter(
            stall=self.stall,
            type='stall_stale',
            metadata__session_id=session.pk,
        )
        self.assertEqual(events.count(), 1)

    def test_merchant_analytics_excludes_cancelled_top_products_and_tracks_paid_attempts(self):
        completed, _ = create_order(self.student, payload(self.stall, self.product))
        merchant_action(completed.pk, self.vendor, 'accept')
        merchant_action(completed.pk, self.vendor, 'ready')
        merchant_action(completed.pk, self.vendor, 'confirm_payment')
        merchant_action(completed.pk, self.vendor, 'complete', completed.pickup_code)

        cancelled, _ = create_order(
            self.student,
            {
                **payload(self.stall, self.product),
                'items': [
                    {
                        'product_id': self.product.pk,
                        'quantity': 4,
                        'expected_price_cents': self.product.price_cents,
                    }
                ],
            },
        )
        merchant_action(cancelled.pk, self.vendor, 'reject', '售罄取消')

        # Compensated duplicate payment order: order remains paid with two verified paid attempts
        dup_order, _ = create_order(self.student, payload(self.stall, self.product))
        now = timezone.now()
        Order.objects.filter(pk=dup_order.pk).update(
            status='completed',
            payment_method='wechat_pay',
            payment_status='paid',
            paid_at=now,
        )
        PaymentAttempt.objects.create(
            order=dup_order,
            merchant=self.stall.merchant,
            mode=dup_order.mode,
            account_key='acct-1',
            mchid='1234567890',
            appid='wx1',
            out_trade_no=uuid.uuid4().hex,
            transaction_id='wx-tx-kept',
            channel='native',
            amount_cents=dup_order.total_cents,
            status='paid',
            paid_at=now,
            expires_at=now + timedelta(minutes=10),
        )
        PaymentAttempt.objects.create(
            order=dup_order,
            merchant=self.stall.merchant,
            mode=dup_order.mode,
            account_key='acct-1',
            mchid='1234567890',
            appid='wx1',
            out_trade_no=uuid.uuid4().hex,
            transaction_id='wx-tx-extra',
            channel='jsapi',
            amount_cents=dup_order.total_cents,
            status='paid',
            paid_at=now,
            expires_at=now + timedelta(minutes=10),
        )

        api = APIClient()
        api.force_authenticate(self.vendor)
        res = api.get(f'/api/v1/merchant/metrics?stall={self.stall.pk}&mode={dup_order.mode}')
        self.assertEqual(res.status_code, 200, res.data)
        top = res.data['top_products']
        self.assertEqual(len(top), 1)
        # Only completed + dup_order count (1 + 1 = 2), NOT the rejected 4-item order!
        self.assertEqual(top[0]['quantity'], 2)
        # Receipts sum offline completed (1x) + 2 verified paid attempts (2x) = 3x product price
        self.assertEqual(res.data['revenue_cents'], self.product.price_cents * 3)
        for row in res.data['recent_payments']:
            if row['id'] == dup_order.pk:
                self.assertFalse(row['refunded'])

    def test_expired_pending_order_rejects_merchant_accept_with_409_order_expired(self):
        order, _ = create_order(self.student, payload(self.stall, self.product))
        Order.objects.filter(pk=order.pk).update(expires_at=timezone.now() - timedelta(minutes=2))
        with self.assertRaises(BusinessError) as ctx:
            merchant_action(order.pk, self.vendor, 'accept')
        self.assertEqual(ctx.exception.detail['code'], 'order_expired')
        order.refresh_from_db()
        self.assertEqual(order.status, 'cancelled')

    def test_logout_exempt_from_mismatched_actor_header_and_account_delete_scrubs_application(self):
        from .models import MerchantApplication

        applicant = User.objects.create_user('applicant-del', password='Password123!')
        app_row = MerchantApplication.objects.create(
            user=applicant,
            business_name='测试个体户',
            stall_name='测试烧烤摊',
            category='烧烤',
            contact_phone='13812345678',
            address_note='北门广场',
            description='炭火烤串',
            confirmed_at=timezone.now(),
        )
        # Historical simulation order must not block account deletion when simulation is disabled
        Order.objects.create(
            user=applicant,
            stall=self.stall,
            stall_name=self.stall.name,
            mode='simulation',
            status='preparing',
            pickup_code='7788',
            total_cents=1200,
            expires_at=timezone.now() + timedelta(minutes=15),
            pickup_address='北门',
            pickup_latitude=30.0,
            pickup_longitude=120.0,
            idempotency_key=uuid.uuid4().hex,
            request_hash='sim-hold-check',
        )

        # Logout succeeds even with mismatched X-Yanhuo-Actor
        session_client = APIClient()
        session_client.force_login(self.student)
        logout_res = session_client.post(
            '/api/v1/auth/logout',
            HTTP_X_YANHUO_ACTOR=f'user:{self.vendor.pk}',
        )
        self.assertEqual(logout_res.status_code, 200)

        with override_settings(SERVICES_SIMULATION_ENABLED=False):
            del_client = APIClient()
            del_client.force_authenticate(applicant)
            del_res = del_client.delete(
                '/api/v1/auth/account',
                {'password': 'Password123!'},
                format='json',
            )
            self.assertEqual(del_res.status_code, 200, del_res.data)

        app_row.refresh_from_db()
        self.assertEqual(app_row.business_name, '')
        self.assertEqual(app_row.stall_name, '')
        self.assertEqual(app_row.contact_phone, '')
        self.assertEqual(app_row.address_note, '')
        self.assertEqual(app_row.description, '')
        self.assertEqual(app_row.status, 'rejected')

