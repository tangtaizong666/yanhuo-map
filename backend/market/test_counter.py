"""One-person counter operations use isolated fixtures, including PG races."""
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import close_old_connections, connection, connections
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .admin import ProductAdmin, ProductInline
from .counter import correct_stock
from .errors import BusinessError
from .models import AuditLog, BusinessSession, Feedback, Order, Product, StockCorrection, Stall
from .services import cancel_order, create_order, expire_pending_orders, merchant_action
from .test_simulation import SimulationSetup
from .tests import fixtures, payload


@override_settings(DEMO_MODE=True, SERVICES_SIMULATION_ENABLED=False)
class CounterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.student, cls.other, cls.vendor, cls.stall, cls.product = fixtures()
        cls.staff = User.objects.create_superuser('counter_admin', password='CounterAdmin!2026')

    def setUp(self):
        cache.clear()
        self.api = APIClient()
        self.api.force_authenticate(self.vendor)

    def correction_data(self, **values):
        return {'stock': 8, 'expected_stock_version': 0, 'idempotency_key': uuid.uuid4().hex,
                'reason': '核对单独预留的线上余量', **values}

    def correction(self, data=None):
        return self.api.post(f'/api/v1/merchant/products/{self.product.pk}/stock-correction', data or self.correction_data(), format='json')

    def profile(self, values):
        return self.api.patch(f'/api/v1/merchant/stalls/{self.stall.pk}/profile', values, format='json')

    def status(self, **values):
        return self.api.post(f'/api/v1/merchant/stalls/{self.stall.pk}/status', {'status': 'open', **values}, format='json')

    def fresh_product(self):
        self.product.refresh_from_db()
        return self.product.stock, self.product.stock_version

    def ready_order(self, user=None):
        order, _ = create_order(user or self.student, payload(self.stall, self.product))
        merchant_action(order.pk, self.vendor, 'accept')
        return merchant_action(order.pk, self.vendor, 'ready')

    def test_defaults_and_public_contract_do_not_change_existing_availability(self):
        data = self.api.get(f'/api/v1/stalls/{self.stall.pk}').data
        self.assertTrue(data['can_order'])
        self.assertEqual(data['receiving_status'], 'unknown')
        self.assertEqual(data['products'][0]['availability'], 'available')
        for key in ('prep_capacity', 'prep_active_orders', 'receiving_seen_at', 'receiving_age_seconds'):
            self.assertNotIn(key, data)
        self.assertNotIn('stock_version', data['products'][0])
        owned = self.api.get('/api/v1/merchant/stalls').data[0]
        self.assertEqual((owned['prep_active_orders'], owned['products'][0]['stock_version']), (0, 0))

    def test_receiving_age_uses_server_clock_and_clamps_future_timestamps(self):
        url = f'/api/v1/stalls/{self.stall.pk}'
        now = timezone.now()
        with patch('market.serializers.timezone.now', return_value=now):
            self.assertEqual(self.api.get(url).data['receiving_valid_for_seconds'], 0)
            Stall.objects.filter(pk=self.stall.pk).update(receiving_seen_at=now - timedelta(seconds=91.25))
            data = self.api.get(url).data
            self.assertEqual(data['receiving_valid_for_seconds'], 0)
            self.assertEqual(data['receiving_status'], 'stale')
            Stall.objects.filter(pk=self.stall.pk).update(receiving_seen_at=now + timedelta(hours=1))
            self.assertEqual(self.api.get(url).data['receiving_valid_for_seconds'], 30)
            response = self.api.post(f'/api/v1/merchant/stalls/{self.stall.pk}/receiving-heartbeat', {}, format='json')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data['receiving_age_seconds'], 0)

    def test_sale_pause_preserves_inventory_and_existing_orders(self):
        original = payload(self.stall, self.product)
        order, _ = create_order(self.student, original)
        result = self.api.patch(f'/api/v1/merchant/products/{self.product.pk}', {'sale_paused': True}, format='json')
        self.assertEqual(result.status_code, 200)
        self.assertEqual((result.data['stock'], result.data['stock_version']), (4, 1))
        with self.assertRaises(BusinessError) as exc: create_order(self.other, payload(self.stall, self.product))
        self.assertEqual(exc.exception.detail['code'], 'product_sale_paused')
        replay, created = create_order(self.student, original)
        self.assertFalse(created); self.assertEqual(replay.pk, order.pk)
        for action in ('accept', 'ready', 'confirm_payment', 'complete'):
            merchant_action(order.pk, self.vendor, action, order.pickup_code)
        self.assertEqual(self.fresh_product(), (4, 1))
        self.api.patch(f'/api/v1/merchant/products/{self.product.pk}', {'sale_paused': False}, format='json')
        self.assertTrue(create_order(self.other, payload(self.stall, self.product))[1])

    def test_product_create_accepts_initial_stock_but_patch_requires_correction(self):
        response = self.api.post(f'/api/v1/merchant/stalls/{self.stall.pk}/products',
            {'name': '新餐点', 'price_cents': 900, 'stock': 7, 'sale_paused': True}, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual((response.data['stock'], response.data['stock_version']), (7, 0))
        result = self.api.patch(f'/api/v1/merchant/products/{self.product.pk}', {'stock': 9, 'name': '不能顺带改名'}, format='json')
        self.assertEqual((result.status_code, result.data['code']), (400, 'stock_edit_requires_correction'))
        self.assertEqual(self.fresh_product(), (5, 0))
        self.assertEqual(self.product.name, '招牌烤冷面')

    def test_stock_version_changes_once_for_reservation_return_and_restock(self):
        order, _ = create_order(self.student, payload(self.stall, self.product, quantity=2))
        self.assertEqual(self.fresh_product(), (3, 1))
        cancel_order(order.pk, self.student, '')
        cancel_order(order.pk, self.student, '')
        self.assertEqual(self.fresh_product(), (5, 2))
        data = {'idempotency_key': uuid.uuid4().hex, 'items': [{'product_id': self.product.pk, 'quantity': 3}]}
        for _ in range(2): self.assertEqual(self.api.post(f'/api/v1/merchant/stalls/{self.stall.pk}/restock', data, format='json').status_code, 200)
        self.assertEqual(self.fresh_product(), (8, 3))

    def test_stock_version_expiry_and_reject_release_once(self):
        order, _ = create_order(self.student, payload(self.stall, self.product))
        Order.objects.filter(pk=order.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(expire_pending_orders(), 1)
        self.assertEqual(expire_pending_orders(), 0)
        self.assertEqual(self.fresh_product(), (5, 2))
        order, _ = create_order(self.other, payload(self.stall, self.product))
        merchant_action(order.pk, self.vendor, 'reject')
        self.assertEqual(self.fresh_product(), (5, 4))

    def test_correction_replays_before_version_check_and_returns_latest_stock(self):
        data = self.correction_data()
        first = self.correction(data)
        self.assertEqual((first.status_code, first.data['replayed']), (200, False))
        self.assertEqual(self.fresh_product(), (8, 1))
        create_order(self.student, payload(self.stall, self.product))
        replay = self.correction(data)
        self.assertEqual((replay.status_code, replay.data['replayed']), (200, True))
        self.assertEqual((replay.data['product']['stock'], replay.data['product']['stock_version']), (7, 2))
        self.assertEqual(StockCorrection.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(action='stock_corrected').count(), 1)
        different = self.correction({**data, 'stock': 10})
        self.assertEqual(different.data['code'], 'idempotency_conflict')

    def test_correction_conflict_requires_fresh_recount_and_does_not_consume_key(self):
        data = self.correction_data()
        create_order(self.student, payload(self.stall, self.product))
        conflict = self.correction(data)
        self.assertEqual((conflict.status_code, conflict.data['code']), (409, 'stock_version_conflict'))
        self.assertEqual(conflict.data['product']['stock_version'], 1)
        self.assertFalse(StockCorrection.objects.exists())
        self.assertEqual(self.correction({**data, 'expected_stock_version': 1}).status_code, 200)
        self.assertEqual(self.fresh_product(), (8, 2))

    def test_correction_uses_available_quantity_without_rewriting_reserved_order(self):
        order, _ = create_order(self.student, payload(self.stall, self.product, quantity=2))
        Product.objects.filter(pk=self.product.pk).update(sale_paused=True)
        self.assertEqual(self.correction(self.correction_data(stock=0, expected_stock_version=1)).status_code, 200)
        self.assertEqual(self.fresh_product(), (0, 2))
        self.assertTrue(self.product.sale_paused)
        self.assertEqual((order.items.get().quantity, order.total_cents), (2, 1600))
        cancel_order(order.pk, self.student, '')
        self.assertEqual(self.fresh_product(), (2, 3))
        self.assertTrue(self.product.sale_paused)

    def test_correction_validation_permissions_and_csrf(self):
        for changes in ({'stock': -1}, {'stock': 100001}, {'reason': ''}, {'expected_stock_version': -1},
                        {'idempotency_key': 'bad'}, {'stock_version': 99}):
            self.assertEqual(self.correction(self.correction_data(**changes)).status_code, 400)
        self.api.force_authenticate(self.other)
        self.assertEqual(self.correction().status_code, 404)
        protected = APIClient(enforce_csrf_checks=True); protected.force_login(self.vendor)
        self.assertEqual(protected.post(f'/api/v1/merchant/products/{self.product.pk}/stock-correction', self.correction_data(), format='json').status_code, 403)
        self.assertEqual(self.fresh_product(), (5, 0))

    def test_admin_correction_reuses_cas_and_inline_cannot_override_stock(self):
        request = RequestFactory().get('/admin/'); request.user = self.staff
        model_admin = ProductAdmin(Product, admin.site)
        self.assertIn('stock', model_admin.get_readonly_fields(request, self.product))
        self.assertIn('stock_version', model_admin.get_readonly_fields(request, self.product))
        self.assertFalse(ProductInline(Product, admin.site).has_change_permission(request))
        stale = Product.objects.get(pk=self.product.pk)
        create_order(self.student, payload(self.stall, self.product))
        stale.name = '新菜名'
        model_admin.save_model(request, stale, type('Form', (), {'changed_data': ['name']})(), True)
        self.assertEqual(self.fresh_product(), (4, 1))
        self.assertEqual(self.product.name, '新菜名')
        client = APIClient(); client.force_login(self.staff)
        url = f'/admin/market/product/{self.product.pk}/correct-stock/'
        self.assertEqual(client.get(url).status_code, 200)
        data = self.correction_data(expected_stock_version=1)
        self.assertEqual(client.post(url, data).status_code, 302)
        self.assertEqual(client.post(url, data).status_code, 302)
        self.assertEqual(self.fresh_product(), (8, 2))
        self.assertEqual(StockCorrection.objects.count(), 1)

    def test_capacity_stops_new_orders_and_ready_releases_a_slot(self):
        self.assertEqual(self.profile({'prep_capacity': 1}).status_code, 200)
        order, _ = create_order(self.student, payload(self.stall, self.product))
        public = self.api.get(f'/api/v1/stalls/{self.stall.pk}').data
        self.assertNotIn('prep_active_orders', public)
        self.assertEqual((public['status'], public['can_order']), ('open', False))
        with self.assertRaises(BusinessError) as exc: create_order(self.other, payload(self.stall, self.product))
        self.assertEqual(exc.exception.detail['code'], 'prep_capacity_reached')
        merchant_action(order.pk, self.vendor, 'accept')
        self.assertFalse(self.api.get(f'/api/v1/stalls/{self.stall.pk}').data['can_order'])
        merchant_action(order.pk, self.vendor, 'ready')
        self.assertTrue(self.api.get(f'/api/v1/stalls/{self.stall.pk}').data['can_order'])
        self.assertTrue(create_order(self.other, payload(self.stall, self.product))[1])

    def test_capacity_lowering_does_not_cancel_orders_and_null_disables_limit(self):
        create_order(self.student, payload(self.stall, self.product))
        create_order(self.other, payload(self.stall, self.product))
        response = self.profile({'prep_capacity': 1})
        self.assertEqual(response.data['prep_active_orders'], 2)
        self.assertEqual(Order.objects.filter(status='pending').count(), 2)
        self.assertEqual(self.profile({'prep_capacity': None}).status_code, 200)
        newcomer = User.objects.create_user('capacity_newcomer', password='ExampleSafe123')
        self.assertTrue(create_order(newcomer, payload(self.stall, self.product))[1])
        for value in (0, -1, 101): self.assertEqual(self.profile({'prep_capacity': value}).status_code, 400)

    def test_cutoff_is_this_session_only_and_does_not_confirm_location_or_close_stall(self):
        confirmed = self.stall.current_session.last_confirmed_at
        order, _ = create_order(self.student, payload(self.stall, self.product))
        result = self.status(stop_orders_at=(timezone.now()-timedelta(seconds=1)).isoformat())
        self.assertEqual((result.status_code, result.data['status'], result.data['can_order']), (200, 'open', False))
        with self.assertRaises(BusinessError) as exc: create_order(self.other, payload(self.stall, self.product))
        self.assertEqual(exc.exception.detail['code'], 'ordering_stopped')
        self.stall.current_session.refresh_from_db()
        self.assertEqual(self.stall.current_session.last_confirmed_at, confirmed)
        merchant_action(order.pk, self.vendor, 'accept')
        merchant_action(order.pk, self.vendor, 'ready')
        old_id = self.stall.current_session_id
        self.status(status='paused')
        self.assertIsNotNone(self.status().data['stop_orders_at'])
        self.status(status='closed')
        reopened = self.status(confirm_location=True)
        self.assertEqual(reopened.status_code, 200)
        self.assertIsNone(reopened.data['stop_orders_at'])
        self.stall.refresh_from_db(); self.assertNotEqual(self.stall.current_session_id, old_id)

    def test_cutoff_validation_and_clearing_preserve_confirmed_location(self):
        past = timezone.now()-timedelta(seconds=1)
        self.status(stop_orders_at=past.isoformat())
        invalid = self.status(stop_orders_at=(timezone.now()+timedelta(hours=3)).isoformat())
        self.assertEqual((invalid.status_code, invalid.data['code']), (400, 'invalid_stop_orders_at'))
        self.stall.current_session.refresh_from_db()
        self.assertEqual(self.stall.current_session.stop_orders_at, past)
        self.assertTrue(self.status(stop_orders_at=None).data['can_order'])

    def test_cutoff_only_preserves_other_devices_pause_and_location(self):
        displayed = self.api.get(f'/api/v1/stalls/{self.stall.pk}').data
        session_id = displayed['business_session_id']
        self.assertEqual(session_id, self.stall.current_session_id)
        confirmed = self.stall.current_session.last_confirmed_at
        original_location = (self.stall.location.address, self.stall.location.updated_at)
        self.status(status='paused')
        cutoff = timezone.now() + timedelta(minutes=30)
        result = self.api.post(f'/api/v1/merchant/stalls/{self.stall.pk}/status', {
            'cutoff_only': True, 'expected_session_id': session_id, 'stop_orders_at': cutoff.isoformat()}, format='json')
        self.assertEqual((result.status_code, result.data['status'], result.data['session_status']), (200, 'paused', 'paused'))
        self.stall.refresh_from_db()
        self.assertEqual(self.stall.current_session.stop_orders_at, cutoff)
        self.assertEqual(self.stall.current_session.last_confirmed_at, confirmed)
        self.assertEqual((self.stall.location.address, self.stall.location.updated_at), original_location)
        self.assertTrue(self.stall.transaction_enabled)

    def test_cutoff_only_rejects_ended_and_old_session_but_allows_new_session(self):
        url = f'/api/v1/merchant/stalls/{self.stall.pk}/status'
        old_id = self.stall.current_session_id
        body = {'cutoff_only': True, 'expected_session_id': old_id,
            'stop_orders_at': (timezone.now()+timedelta(minutes=30)).isoformat()}
        self.status(status='closed')
        result = self.api.post(url, body, format='json')
        self.assertEqual((result.status_code, result.data['code']), (409, 'business_session_ended'))
        new_id = self.status(confirm_location=True).data['business_session_id']
        self.assertNotEqual(new_id, old_id)
        result = self.api.post(url, body, format='json')
        self.assertEqual((result.status_code, result.data['code']), (409, 'business_session_changed'))
        self.assertIsNone(BusinessSession.objects.get(pk=new_id).stop_orders_at)
        body['expected_session_id'] = new_id
        result = self.api.post(url, body, format='json')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data['business_session_id'], new_id)
        self.assertIsNotNone(result.data['stop_orders_at'])
        body['stop_orders_at'] = None
        self.assertIsNone(self.api.post(url, body, format='json').data['stop_orders_at'])

    def test_cutoff_only_rejects_mixed_operations_missing_identity_and_foreign_stall(self):
        url = f'/api/v1/merchant/stalls/{self.stall.pk}/status'
        body = {'cutoff_only': True, 'expected_session_id': self.stall.current_session_id, 'stop_orders_at': None}
        for extra in ({'status': 'open'}, {'confirm_location': False}, {'address': '不能修改'}, {'closes_at': None},
                      {'cutoff_only': False}, {'expected_session_id': None}):
            self.assertEqual(self.api.post(url, {**body, **extra}, format='json').status_code, 400)
        for field in ('expected_session_id', 'stop_orders_at'):
            self.assertEqual(self.api.post(url, {k: v for k, v in body.items() if k != field}, format='json').status_code, 400)
        invalid = {**body, 'stop_orders_at': (timezone.now()+timedelta(hours=3)).isoformat()}
        result = self.api.post(url, invalid, format='json')
        self.assertEqual((result.status_code, result.data['code']), (400, 'invalid_stop_orders_at'))
        self.api.force_authenticate(self.other)
        self.assertEqual(self.api.post(url, body, format='json').status_code, 404)

    def test_cutoff_only_rejects_expired_or_missing_session_without_creating_one(self):
        url = f'/api/v1/merchant/stalls/{self.stall.pk}/status'
        body = {'cutoff_only': True, 'expected_session_id': self.stall.current_session_id, 'stop_orders_at': None}
        count = BusinessSession.objects.count()
        BusinessSession.objects.filter(pk=self.stall.current_session_id).update(closes_at=timezone.now()-timedelta(seconds=1))
        result = self.api.post(url, body, format='json')
        self.assertEqual((result.status_code, result.data['code']), (409, 'business_session_ended'))
        Stall.objects.filter(pk=self.stall.pk).update(current_session=None)
        result = self.api.post(url, body, format='json')
        self.assertEqual((result.status_code, result.data['code']), (409, 'business_session_changed'))
        self.assertIsNone(self.api.get(f'/api/v1/stalls/{self.stall.pk}').data['business_session_id'])
        self.assertEqual(BusinessSession.objects.count(), count)

    def test_heartbeat_is_monotonic_advisory_and_never_confirms_location(self):
        url = f'/api/v1/merchant/stalls/{self.stall.pk}/receiving-heartbeat'
        confirmed = self.stall.current_session.last_confirmed_at
        now = timezone.now()
        with patch('market.counter.timezone.now', return_value=now):
            result = self.api.post(url, {}, format='json')
        self.assertEqual(result.status_code, 200)
        with patch('market.counter.timezone.now', return_value=now-timedelta(seconds=1)):
            self.api.post(url, {}, format='json')
        self.stall.refresh_from_db()
        self.assertEqual(self.stall.receiving_seen_at, now)
        self.assertEqual(self.stall.current_session.last_confirmed_at, confirmed)
        Stall.objects.filter(pk=self.stall.pk).update(receiving_seen_at=now-timedelta(seconds=91))
        public = self.api.get(f'/api/v1/stalls/{self.stall.pk}').data
        self.assertEqual(public['receiving_status'], 'stale'); self.assertTrue(public['can_order'])
        self.assertEqual(self.api.post(url, {'receiving_seen_at': now.isoformat()}, format='json').status_code, 400)
        self.api.force_authenticate(self.other)
        self.assertEqual(self.api.post(url, {}, format='json').status_code, 404)

    def test_order_contact_survives_public_stall_hiding_and_uses_current_phone(self):
        order, _ = create_order(self.student, payload(self.stall, self.product))
        self.profile({'contact_phone': '13800138000'})
        Stall.objects.filter(pk=self.stall.pk).update(is_visible=False)
        self.api.force_authenticate(self.student)
        self.assertEqual(self.api.get(f'/api/v1/stalls/{self.stall.pk}').status_code, 404)
        self.assertEqual(self.api.get(f'/api/v1/orders/{order.pk}').data['merchant_contact_phone'], '13800138000')
        self.api.force_authenticate(self.other)
        self.assertEqual(self.api.get(f'/api/v1/orders/{order.pk}').status_code, 404)

    def test_order_feedback_is_owner_scoped_works_when_hidden_and_deduplicates(self):
        order, _ = create_order(self.student, payload(self.stall, self.product))
        Stall.objects.filter(pk=self.stall.pk).update(is_visible=False)
        data = {'order_id': str(order.pk), 'content': '取餐联系不上', 'idempotency_key': uuid.uuid4().hex}
        self.api.force_authenticate(self.student)
        first = self.api.post('/api/v1/feedback', data, format='json')
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(self.api.post('/api/v1/feedback', data, format='json').status_code, 200)
        report = Feedback.objects.get()
        self.assertEqual((report.order_id, report.stall_id, report.user_id), (order.pk, self.stall.pk, self.student.pk))
        mismatch = self.api.post('/api/v1/feedback', {**data, 'idempotency_key': uuid.uuid4().hex, 'stall_id': 999}, format='json')
        self.assertEqual(mismatch.status_code, 400)
        self.api.force_authenticate(self.other)
        self.assertEqual(self.api.post('/api/v1/feedback', data, format='json').status_code, 404)
        self.api.force_authenticate(None)
        self.assertEqual(self.api.post('/api/v1/feedback', data, format='json').status_code, 403)

    def test_pickup_lookup_is_read_only_masked_and_limited_to_own_ready_pickups(self):
        order = self.ready_order()
        url = f'/api/v1/merchant/stalls/{self.stall.pk}/pickup-lookup'
        result = self.api.post(url, {'pickup_code': order.pickup_code}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual((result.data['id'], result.data['pickup_code'], result.data['status'], result.data['payment_status']),
            (str(order.pk), '', 'ready', 'unpaid'))
        self.assertNotIn(order.pickup_code, json.dumps(list(AuditLog.objects.values('details'))))
        Order.objects.filter(pk=order.pk).update(status='completed')
        self.assertEqual(self.api.post(url, {'pickup_code': order.pickup_code}, format='json').data['code'], 'pickup_order_not_found')
        Order.objects.filter(pk=order.pk).update(status='ready', fulfillment_type='delivery')
        self.assertEqual(self.api.post(url, {'pickup_code': order.pickup_code}, format='json').status_code, 404)
        self.api.force_authenticate(self.other)
        self.assertEqual(self.api.post(url, {'pickup_code': order.pickup_code}, format='json').status_code, 404)

    def test_pickup_lookup_collision_requires_full_number_without_listing_matches(self):
        first, second = self.ready_order(), self.ready_order(self.other)
        Order.objects.filter(pk=second.pk).update(pickup_code=first.pickup_code)
        url = f'/api/v1/merchant/stalls/{self.stall.pk}/pickup-lookup'
        ambiguous = self.api.post(url, {'pickup_code': first.pickup_code}, format='json')
        self.assertEqual((ambiguous.status_code, ambiguous.data['code']), (409, 'pickup_code_ambiguous'))
        self.assertNotIn(first.number, str(ambiguous.data))
        result = self.api.post(url, {'pickup_code': first.pickup_code, 'number': second.number}, format='json')
        self.assertEqual(result.data['id'], str(second.pk))

    def test_pickup_lookup_requires_exact_code_and_throttles_attempts(self):
        url = f'/api/v1/merchant/stalls/{self.stall.pk}/pickup-lookup'
        for code in ('1234', 'abcdefgh', '１２３４５６７８'):
            self.assertEqual(self.api.post(url, {'pickup_code': code}, format='json').status_code, 400)
        cache.clear()
        with patch('market.counter.PickupLookupThrottle.rate', '2/minute'):
            self.assertEqual(self.api.post(url, {'pickup_code': '12345678'}, format='json').status_code, 404)
            self.assertEqual(self.api.post(url, {'pickup_code': '12345678'}, format='json').status_code, 404)
            self.assertEqual(self.api.post(url, {'pickup_code': '12345678'}, format='json').status_code, 429)


@override_settings(SERVICES_SIMULATION_ENABLED=True, DEMO_MODE=True, PRODUCTION=False)
class CounterPaymentTests(SimulationSetup, TestCase):
    def test_reserved_delivery_payment_survives_capacity_cutoff_and_product_pause(self):
        self.stall.prep_capacity = 1; self.stall.save(update_fields=['prep_capacity'])
        order = self.create()
        self.assertEqual(order.status, 'pending_payment')
        self.assertFalse(Stall.objects.get(pk=self.stall.pk).can_order())
        BusinessSession.objects.filter(pk=self.stall.current_session_id).update(stop_orders_at=timezone.now()-timedelta(seconds=1))
        Product.objects.filter(pk=self.product.pk).update(sale_paused=True)
        payment = self.pay(order)
        self.assertEqual((order.status, order.payment_status), ('pending', 'paid'))
        self.assertFalse(hasattr(order, 'payment_refund'))
        from .simulation import simulate_payment
        simulate_payment(order.pk, self.student, payment.pk, 'success')
        self.product.refresh_from_db()
        self.assertEqual((self.product.stock, self.product.stock_version), (4, 1))
        merchant_action(order.pk, self.vendor, 'accept')
        merchant_action(order.pk, self.vendor, 'ready')
        self.assertEqual(Stall.objects.get(pk=self.stall.pk).prep_active_orders(), 0)

    def test_actual_closed_delivery_payment_still_refunds_and_returns_version_once(self):
        order = self.create()
        self.start(order)
        BusinessSession.objects.filter(pk=self.stall.current_session_id).update(status='closed')
        from .simulation import simulate_payment
        payment = order.payments.first()
        simulate_payment(order.pk, self.student, payment.pk, 'success')
        order.refresh_from_db(); self.product.refresh_from_db()
        self.assertEqual((order.status, order.payment_status), ('cancelled', 'refunding'))
        self.assertEqual((self.product.stock, self.product.stock_version), (5, 2))
        simulate_payment(order.pk, self.student, payment.pk, 'success')
        self.product.refresh_from_db(); self.assertEqual(self.product.stock_version, 2)


@skipUnless(connection.vendor == 'postgresql', 'Counter row-lock races require PostgreSQL')
class CounterConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.student, self.other, self.vendor, self.stall, self.product = fixtures()

    def race(self, first, second):
        gate = Barrier(2)
        def run(task):
            close_old_connections()
            try:
                gate.wait(timeout=10)
                try: return ('ok', task())
                except BusinessError as exc: return (exc.detail['code'], None)
            finally: connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = pool.submit(run, first), pool.submit(run, second)
            return a.result(timeout=30), b.result(timeout=30)

    def correction(self, stock=9, version=0, key='concurrent-correction'):
        return correct_stock(self.product.pk, self.vendor, {'stock': stock, 'expected_stock_version': version,
            'idempotency_key': key, 'reason': '重新核对线上余量'})

    def test_last_preparation_slot_has_only_one_winner(self):
        Stall.objects.filter(pk=self.stall.pk).update(prep_capacity=1)
        a, b = self.race(lambda: create_order(self.student, payload(self.stall, self.product)),
            lambda: create_order(self.other, payload(self.stall, self.product)))
        self.assertCountEqual([a[0], b[0]], ['ok', 'prep_capacity_reached'])
        self.product.refresh_from_db()
        self.assertEqual((Order.objects.count(), self.product.stock, self.product.stock_version), (1, 4, 1))

    def test_correction_and_checkout_cannot_overwrite_the_new_reservation(self):
        a, b = self.race(lambda: self.correction(), lambda: create_order(self.student, payload(self.stall, self.product)))
        self.assertEqual(b[0], 'ok')
        self.product.refresh_from_db()
        if a[0] == 'ok': self.assertEqual((self.product.stock, self.product.stock_version), (8, 2))
        else:
            self.assertEqual(a[0], 'stock_version_conflict')
            self.assertEqual((self.product.stock, self.product.stock_version), (4, 1))

    def test_same_correction_key_concurrently_applies_once(self):
        a, b = self.race(lambda: self.correction(), lambda: self.correction())
        self.assertEqual((a[0], b[0]), ('ok', 'ok'))
        self.assertCountEqual([a[1][1], b[1][1]], [False, True])
        self.product.refresh_from_db()
        self.assertEqual((self.product.stock, self.product.stock_version, StockCorrection.objects.count()), (9, 1, 1))

    def test_correction_and_cancellation_preserve_returned_reservations(self):
        order, _ = create_order(self.student, payload(self.stall, self.product, quantity=2))
        a, b = self.race(lambda: self.correction(stock=7, version=1), lambda: cancel_order(order.pk, self.student, '取消'))
        self.assertEqual(b[0], 'ok')
        self.product.refresh_from_db()
        if a[0] == 'ok': self.assertEqual((self.product.stock, self.product.stock_version), (9, 3))
        else:
            self.assertEqual(a[0], 'stock_version_conflict')
            self.assertEqual((self.product.stock, self.product.stock_version), (5, 2))

    def test_correction_and_restock_do_not_overwrite_additions(self):
        def restock():
            api = APIClient(); api.force_authenticate(self.vendor)
            result = api.post(f'/api/v1/merchant/stalls/{self.stall.pk}/restock', {
                'idempotency_key': 'race-restock-key', 'items': [{'product_id': self.product.pk, 'quantity': 3}]}, format='json')
            self.assertEqual(result.status_code, 200)
        a, b = self.race(lambda: self.correction(), restock)
        self.assertEqual(b[0], 'ok')
        self.product.refresh_from_db()
        if a[0] == 'ok': self.assertEqual((self.product.stock, self.product.stock_version), (12, 2))
        else:
            self.assertEqual(a[0], 'stock_version_conflict')
            self.assertEqual((self.product.stock, self.product.stock_version), (8, 1))

    def test_capacity_release_racing_checkout_never_exceeds_limit(self):
        Stall.objects.filter(pk=self.stall.pk).update(prep_capacity=1)
        order, _ = create_order(self.student, payload(self.stall, self.product))
        merchant_action(order.pk, self.vendor, 'accept')
        a, b = self.race(lambda: merchant_action(order.pk, self.vendor, 'ready'),
            lambda: create_order(self.other, payload(self.stall, self.product)))
        self.assertEqual(a[0], 'ok')
        self.assertIn(b[0], ('ok', 'prep_capacity_reached'))
        self.assertLessEqual(Stall.objects.get(pk=self.stall.pk).prep_active_orders(), 1)
