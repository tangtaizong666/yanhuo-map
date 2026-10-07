import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from django.contrib.auth.models import User
from django.db import close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from .errors import BusinessError
from .models import Area, BusinessSession, Event, MerchantProfile, Order, Product, Review, SiteConfiguration, Stall, StallLocation
from .services import cancel_order, create_order, expire_pending_orders, merchant_action


def fixtures():
    student = User.objects.create_user('tester', password='DemoStrong123', first_name='小宇')
    other = User.objects.create_user('other', password='DemoStrong123')
    vendor = User.objects.create_user('merchant', password='DemoStrong123')
    # Storefront tier keeps the shared fixture on the platform-payment path; mobile vendors have their own tests.
    merchant = MerchantProfile.objects.create(user=vendor, business_name='测试商户', is_verified=True,
        qualification_tier='storefront', licensed_business_address='测试门店', food_preparation_address='测试门店后厨',
        license_number='TEST-ONLY-NOT-A-REAL-LICENSE', license_valid_until=timezone.localdate()+timedelta(days=365))
    area = Area.objects.create(name='测试校园', latitude=31.23, longitude=121.47)
    stall = Stall.objects.create(merchant=merchant, area=area, name='测试烤冷面', category='小吃', transaction_enabled=True)
    StallLocation.objects.create(stall=stall, address='校园南门', latitude=31.23, longitude=121.47)
    session = BusinessSession.objects.create(stall=stall, status='open', closes_at=timezone.now()+timedelta(hours=2))
    stall.current_session = session
    stall.save()
    product = Product.objects.create(stall=stall, name='招牌烤冷面', price_cents=800, stock=5)
    return student, other, vendor, stall, product


def payload(stall, product, quantity=1, key=None):
    return {'stall_id': stall.pk, 'items': [{'product_id': product.pk, 'quantity': quantity,
        'expected_price_cents': product.price_cents}], 'note': '', 'contact_phone': '', 'idempotency_key': key or str(uuid.uuid4())}


class MarketTests(TestCase):
    def setUp(self):
        self.student, self.other, self.vendor, self.stall, self.product = fixtures()
        self.client = APIClient()
        self.client.force_authenticate(self.student)

    def create(self, **kwargs):
        data = payload(self.stall, self.product)
        data.update(kwargs)
        response = self.client.post('/api/v1/orders', data, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def action(self, order, action, **kwargs):
        return merchant_action(order['id'], self.vendor, action, kwargs.get('code', ''), kwargs.get('reason', ''))

    def test_complete_flow_persists_price_payment_review(self):
        order = self.create(note='少辣')
        self.assertEqual(order['total_cents'], 800)
        self.assertEqual(order['payment_status'], 'unpaid')
        self.action(order, 'accept')
        self.action(order, 'ready')
        with self.assertRaises(BusinessError): self.action(order, 'complete', code=order['pickup_code'])
        self.action(order, 'confirm_payment')
        order = self.client.get(f"/api/v1/orders/{order['id']}").data
        with self.assertRaises(BusinessError): self.action(order, 'complete', code='00000000')
        completed = self.action(order, 'complete', code=order['pickup_code'])
        self.assertEqual(completed.status, 'completed')
        with self.assertRaises(BusinessError): self.action(order, 'complete', code=order['pickup_code'])
        response = self.client.post(f"/api/v1/orders/{order['id']}/review", {'rating': 5, 'content': '很香，下次还来。'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['review']['rating'], 5)
        again = self.client.post(f"/api/v1/orders/{order['id']}/review", {'rating': 4}, format='json')
        self.assertEqual(again.status_code, 409)
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 4)

    def test_early_review_and_foreign_order_are_forbidden(self):
        order = self.create()
        early = self.client.post(f"/api/v1/orders/{order['id']}/review", {'rating': 5}, format='json')
        self.assertEqual(early.status_code, 409)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(f"/api/v1/orders/{order['id']}").status_code, 404)
        self.assertEqual(self.client.post(f"/api/v1/orders/{order['id']}/cancel", {}, format='json').status_code, 404)
        self.assertEqual(self.client.post(f"/api/v1/merchant/orders/{order['id']}/action", {'action': 'accept'}, format='json').status_code, 404)

    def test_idempotency_same_body_does_not_reserve_twice(self):
        data = payload(self.stall, self.product)
        first = self.client.post('/api/v1/orders', data, format='json')
        second = self.client.post('/api/v1/orders', data, format='json')
        self.assertEqual((first.status_code, second.status_code), (201, 200))
        self.assertEqual(first.data['id'], second.data['id'])
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 4)
        data['note'] = 'different'
        self.assertEqual(self.client.post('/api/v1/orders', data, format='json').status_code, 409)

    def test_expired_retry_returns_original_and_expiry_restores_once(self):
        data = payload(self.stall, self.product)
        order, _ = create_order(self.student, data)
        Order.objects.filter(pk=order.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(expire_pending_orders(), 1)
        self.assertEqual(expire_pending_orders(), 0)
        original, created = create_order(self.student, data)
        self.assertFalse(created)
        self.assertEqual(original.status, 'cancelled')
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 5)
        self.assertEqual(Event.objects.filter(type='order_expired').count(), 1)

    def test_pending_cancel_releases_once(self):
        order = self.create()
        a = cancel_order(order['id'], self.student, '不需要了')
        b = cancel_order(order['id'], self.student, '再次取消')
        self.assertEqual((a.status, b.status), ('cancelled', 'cancelled'))
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 5)
        with self.assertRaises(BusinessError): self.action(order, 'accept')

    def test_cancel_after_accept_requires_merchant_and_blocks_payment(self):
        order = self.create()
        self.action(order, 'accept')
        self.action(order, 'ready')
        pending = cancel_order(order['id'], self.student, '临时有事')
        self.assertTrue(pending.cancel_requested)
        self.assertEqual(pending.status, 'ready')
        with self.assertRaises(BusinessError): self.action(order, 'confirm_payment')
        self.action(order, 'approve_cancel')
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 5)

    def test_cancel_while_preparing_returns_stock(self):
        order = self.create()
        self.action(order, 'accept')
        cancel_order(order['id'], self.student, '临时有事')
        self.action(order, 'approve_cancel')
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 5)

    def test_cancel_after_payment_blocked(self):
        order = self.create()
        self.action(order, 'accept')
        self.action(order, 'ready')
        self.action(order, 'confirm_payment')
        with self.assertRaises(BusinessError): cancel_order(order['id'], self.student, '')
        with self.assertRaises(BusinessError): self.action(order, 'approve_cancel')

    def test_reject_and_deny_cancel(self):
        order = self.create()
        self.action(order, 'reject', reason='售罄')
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 5)
        next_order = self.create()
        self.action(next_order, 'accept')
        cancel_order(next_order['id'], self.student, '')
        denied = self.action(next_order, 'deny_cancel', reason='已经开始制作')
        self.assertFalse(denied.cancel_requested)
        self.assertEqual(denied.status, 'preparing')

    def test_stale_closed_paused_and_unverified_block_checkout(self):
        for state in ['closed', 'paused', 'stale', 'unverified', 'display_only']:
            with self.subTest(state=state):
                session = self.stall.current_session
                session.status = state if state in ('closed', 'paused') else 'open'
                session.last_confirmed_at = timezone.now()-timedelta(minutes=61) if state == 'stale' else timezone.now()
                session.save()
                self.stall.transaction_enabled = state != 'display_only'
                self.stall.save()
                self.stall.merchant.is_verified = state != 'unverified'
                self.stall.merchant.save()
                response = self.client.post('/api/v1/orders', payload(self.stall, self.product), format='json')
                self.assertEqual(response.status_code, 409, response.data)
                self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 5)
                if state == 'unverified':
                    self.assertFalse(self.client.get(f'/api/v1/stalls/{self.stall.pk}').data['transaction_enabled'])

    def test_stale_threshold_configurable_and_closing_time_respected(self):
        SiteConfiguration.objects.create(stale_minutes=3)
        self.stall.current_session.last_confirmed_at = timezone.now()-timedelta(minutes=4)
        self.stall.current_session.save()
        self.assertEqual(self.stall.effective_status(), 'stale')
        self.stall.current_session.closes_at = timezone.now()-timedelta(minutes=1)
        self.stall.current_session.save()
        self.assertEqual(self.stall.effective_status(), 'closed')

    def test_price_changed_stock_shortage_and_cross_stall_no_mutations(self):
        data = payload(self.stall, self.product)
        Product.objects.filter(pk=self.product.pk).update(price_cents=900)
        response = self.client.post('/api/v1/orders', data, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'price_changed')
        self.product.refresh_from_db()
        shortage = payload(self.stall, self.product, quantity=6)
        self.assertEqual(self.client.post('/api/v1/orders', shortage, format='json').data['code'], 'out_of_stock')
        other_stall = Stall.objects.create(merchant=self.stall.merchant, area=self.stall.area, name='其他摊位', category='小吃')
        other_product = Product.objects.create(stall=other_stall, name='煎饼', stock=10, price_cents=500)
        bad = payload(self.stall, other_product)
        self.assertEqual(self.client.post('/api/v1/orders', bad, format='json').status_code, 400)
        self.assertFalse(Order.objects.exists())

    def test_bad_quantities_duplicate_ids_and_amount_validation(self):
        for quantity in [0, -1, 100, 1.5]:
            data = payload(self.stall, self.product, quantity=quantity)
            self.assertEqual(self.client.post('/api/v1/orders', data, format='json').status_code, 400)
        data = payload(self.stall, self.product)
        data['items'] *= 2
        self.assertEqual(self.client.post('/api/v1/orders', data, format='json').status_code, 400)

    def test_location_change_disables_transactions_and_keeps_order_snapshot(self):
        order = self.create()
        self.client.force_authenticate(self.vendor)
        response = self.client.post(f'/api/v1/merchant/stalls/{self.stall.pk}/status',
            {'status': 'open', 'confirm_location': True, 'address': '校园东门', 'latitude': 31.234, 'longitude': 121.476}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data['transaction_enabled'])
        self.client.force_authenticate(self.student)
        existing = self.client.get(f"/api/v1/orders/{order['id']}").data
        self.assertEqual(existing['pickup_address'], '校园南门')
        self.assertEqual(existing['current_address'], '校园东门')
        self.assertTrue(existing['location_changed'])

    def test_merchant_only_owns_own_products_and_no_self_verification(self):
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.patch(f'/api/v1/merchant/products/{self.product.pk}', {'stock': 100}, format='json').status_code, 404)
        self.client.force_authenticate(self.vendor)
        self.stall.transaction_enabled = False
        self.stall.save()
        response = self.client.post(f'/api/v1/merchant/stalls/{self.stall.pk}/status',
            {'status': 'open', 'confirm_location': True, 'transaction_enabled': True}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['transaction_enabled'])
        self.assertEqual(self.client.patch(f'/api/v1/merchant/products/{self.product.pk}', {'stock': -1}, format='json').status_code, 400)

    def test_merchant_cannot_read_pickup_code(self):
        order = self.create()
        self.client.force_authenticate(self.vendor)
        listed = self.client.get('/api/v1/merchant/orders').data
        self.assertEqual(listed[0]['pickup_code'], '')
        action = self.client.post(f"/api/v1/merchant/orders/{order['id']}/action", {'action': 'accept'}, format='json')
        self.assertEqual(action.data['pickup_code'], '')

    def test_follow_and_search_persist_without_fake_distance_or_reviews(self):
        self.assertEqual(self.client.post(f'/api/v1/stalls/{self.stall.pk}/follow').status_code, 200)
        self.assertEqual(self.client.post(f'/api/v1/stalls/{self.stall.pk}/follow').status_code, 200)
        result = self.client.get('/api/v1/follows').data['results']
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]['is_followed'])
        self.assertIsNone(result[0]['distance_m'])
        self.assertIsNone(result[0]['rating'])
        self.assertEqual(result[0]['review_count'], 0)
        results = self.client.get('/api/v1/stalls', {'q': '招牌', 'lat': 31.23, 'lng': 121.47, 'sort': 'distance'}).data['results']
        self.assertEqual(results[0]['distance_m'], 0)
        self.client.delete(f'/api/v1/stalls/{self.stall.pk}/follow')
        self.assertEqual(self.client.get('/api/v1/follows').data['results'], [])

    def test_invalid_nan_coordinate_rejected(self):
        self.assertEqual(self.client.get('/api/v1/stalls?lat=nan&lng=121.4').status_code, 400)
        self.assertEqual(self.client.get('/api/v1/stalls?lat=31.2').status_code, 400)

    @override_settings(DEMO_MODE=False)
    def test_demo_stalls_hidden_from_production(self):
        self.stall.is_demo = True
        self.stall.save()
        self.assertEqual(self.client.get('/api/v1/stalls').data['results'], [])
        self.assertEqual(self.client.get(f'/api/v1/stalls/{self.stall.pk}').status_code, 404)
        self.assertEqual(self.client.post('/api/v1/orders', payload(self.stall, self.product), format='json').status_code, 404)

    def test_account_deactivation_rejects_active_orders_and_keeps_history(self):
        order = self.create()
        self.assertEqual(self.client.delete('/api/v1/auth/account', {'password': 'DemoStrong123'}, format='json').status_code, 409)
        cancel_order(order['id'], self.student, '')
        self.assertEqual(self.client.delete('/api/v1/auth/account', {'password': 'wrong'}, format='json').status_code, 400)
        self.assertEqual(self.client.delete('/api/v1/auth/account', {'password': 'DemoStrong123'}, format='json').status_code, 200)
        self.student.refresh_from_db()
        self.assertFalse(self.student.is_active)
        self.assertEqual(Order.objects.count(), 1)

    def test_auth_session_csrf_registration_password_change(self):
        client = APIClient(enforce_csrf_checks=True)
        self.assertEqual(client.post('/api/v1/auth/login', {'username': 'tester', 'password': 'DemoStrong123'}).status_code, 403)
        client.get('/api/v1/auth/csrf')
        token = client.cookies['csrftoken'].value
        response = client.post('/api/v1/auth/login', {'username': 'tester', 'password': 'DemoStrong123'}, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(client.get('/api/v1/auth/me').json()['username'], 'tester')
        token = client.cookies['csrftoken'].value
        self.assertEqual(client.post('/api/v1/auth/password', {'old_password': 'DemoStrong123', 'new_password': 'OtherStrong123'}, HTTP_X_CSRFTOKEN=token).status_code, 200)
        self.assertEqual(client.post('/api/v1/auth/logout', HTTP_X_CSRFTOKEN=token).status_code, 200)
        self.assertIsNone(client.get('/api/v1/auth/me').json())
        client.get('/api/v1/auth/csrf')
        token = client.cookies['csrftoken'].value
        response = client.post('/api/v1/auth/register', {'username': 'new_student', 'password': 'AnotherStrong123', 'display_name': '新同学'}, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['display_name'], '新同学')

    def test_feedback_metrics_and_map_configuration(self):
        self.assertEqual(self.client.post('/api/v1/feedback', {'content': '希望增加无辣选项'}, format='json').status_code, 201)
        self.assertEqual(self.client.get('/api/v1/merchant/metrics').status_code, 403)
        self.client.force_authenticate(self.vendor)
        self.assertEqual(self.client.get('/api/v1/merchant/metrics').status_code, 200)
        config = self.client.get('/api/v1/config').data
        self.assertEqual(config['amap_key'], '')
        self.assertNotIn('amap_security_code', config)
        self.assertEqual(self.client.get('/api/v1/amap-proxy/_AMapService/v3/ip').status_code, 503)

    @override_settings(AMAP_KEY='test-key', AMAP_SECURITY_CODE='top-secret')
    def test_map_proxy_restricts_host_path_origin_callback(self):
        base = '/api/v1/amap-proxy/_AMapService/'
        self.assertEqual(self.client.get(base+'v3/ip').status_code, 403)
        self.assertEqual(self.client.get(base+'https://evil.example/v3/ip', HTTP_REFERER='http://testserver/').status_code, 400)
        self.assertEqual(self.client.get(base+'v3/ip?callback=alert(1)', HTTP_REFERER='http://testserver/').status_code, 400)
        self.assertEqual(self.client.get(base+'v3/ip', HTTP_ORIGIN='https://evil.example').status_code, 403)


@skipUnless(connection.vendor == 'postgresql', 'Real row-lock concurrency tests require PostgreSQL; SQLite demo is not production.')
class PostgreSQLConcurrencyTests(TransactionTestCase):
    reset_sequences = True
    def setUp(self): self.student, self.other, self.vendor, self.stall, self.product = fixtures()
    def race(self, left, right):
        barrier = Barrier(2)
        def invoke(fn):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return fn()
            except BusinessError as exc: return exc.detail['code']
            finally: connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = pool.submit(invoke, left), pool.submit(invoke, right)
            return a.result(timeout=30), b.result(timeout=30)
    def test_last_item_cannot_be_sold_twice(self):
        Product.objects.filter(pk=self.product.pk).update(stock=1)
        data1, data2 = payload(self.stall, self.product), payload(self.stall, self.product)
        self.race(lambda: create_order(self.student, data1), lambda: create_order(self.other, data2))
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 0)
    def test_simultaneous_duplicate_submission_reserves_once(self):
        data = payload(self.stall, self.product)
        self.race(lambda: create_order(self.student, data), lambda: create_order(self.student, data))
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 4)
    def test_accept_cancel_race_has_consistent_inventory(self):
        order, _ = create_order(self.student, payload(self.stall, self.product))
        self.race(lambda: merchant_action(order.pk, self.vendor, 'accept'), lambda: cancel_order(order.pk, self.student, '取消'))
        order.refresh_from_db()
        stock = Product.objects.get(pk=self.product.pk).stock
        self.assertIn((order.status, order.cancel_requested, stock), [('cancelled', False, 5), ('preparing', True, 4)])
    def test_checkout_and_cancellation_same_user_preserve_stock(self):
        order, _ = create_order(self.student, payload(self.stall, self.product))
        self.race(lambda: create_order(self.student, payload(self.stall, self.product)),
            lambda: cancel_order(order.pk, self.student, '改点另一单'))
        self.assertEqual(Order.objects.filter(status='cancelled').count(), 1)
        self.assertEqual(Order.objects.filter(status='pending').count(), 1)
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 4)
