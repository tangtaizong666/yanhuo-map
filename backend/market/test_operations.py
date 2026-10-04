"""Onboarding, discovery, inventory and reporting contracts; fixtures only."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth.models import Permission, User
from django.core.cache import cache
from django.db import close_old_connections, connection, connections
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .admin import ApplicationAdmin
from .errors import BusinessError
from .models import (BusinessSession, Event, Feedback, MerchantApplication, MerchantProfile,
    Order, Product, RestockBatch, SiteConfiguration, Stall, StallLocation)
from .operations import review_application
from .services import cancel_order, create_order, merchant_action
from .tests import fixtures, payload


@override_settings(DEMO_MODE=True, SERVICES_SIMULATION_ENABLED=False, PRODUCTION=False)
class OperationsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.student, cls.other, cls.vendor, cls.stall, cls.product = fixtures()
        cls.staff = User.objects.create_user('operator', is_staff=True)
        cls.staff.user_permissions.add(Permission.objects.get(content_type__app_label='market', codename='change_merchantapplication'))

    def setUp(self):
        cache.clear()
        self.api = APIClient()
        self.api.force_authenticate(self.vendor)

    def application_values(self):
        return dict(business_name='陈家经营主体', stall_name='陈家饭摊', contact_phone='13800138000',
            area_id=self.stall.area_id, category='正餐', address_note='南门绿色棚顶', description='手工现做')

    def application(self, source='self'):
        return MerchantApplication.objects.create(user=self.student, source=source, **self.application_values())

    def submit(self):
        self.api.force_authenticate(self.student)
        return self.api.post('/api/v1/merchant/application/submit', {}, format='json')

    def restock(self, items=None, key=None, stall=None):
        return self.api.post(f'/api/v1/merchant/stalls/{(stall or self.stall).pk}/restock',
            {'idempotency_key': key or uuid.uuid4().hex,
             'items': items or [{'product_id': self.product.pk, 'quantity': 3}]}, format='json')

    def report_values(self):
        return dict(kind='not_found', stall_id=self.stall.pk, idempotency_key=uuid.uuid4().hex,
            content='请联系我 13900001234，小王', contact='13900001234',
            location_snapshot={'address': '当时展示的旧地址', 'latitude': 31.23, 'longitude': 121.47,
                               'last_confirmed_at': timezone.now().isoformat()})

    def historical_order(self, user=None, stall=None, **changes):
        value = dict(user=user or self.student, stall=stall or self.stall, stall_name='历史摊位',
            status='completed', mode='live', total_cents=800, payment_status='paid',
            created_at=timezone.now()-timedelta(hours=2), completed_at=timezone.now()-timedelta(hours=1),
            paid_at=timezone.now()-timedelta(hours=1), expires_at=timezone.now()+timedelta(hours=1),
            pickup_address='历史地点', pickup_latitude=31.23, pickup_longitude=121.47,
            idempotency_key=uuid.uuid4().hex, request_hash='operations-test')
        return Order.objects.create(**{**value, **changes})

    def test_arrival_details_and_location_draft_do_not_replace_confirmed_location_or_food_image(self):
        old_image, old_location = self.stall.image, self.stall.location.address
        response = self.api.patch(f'/api/v1/merchant/stalls/{self.stall.pk}/profile', {
            'arrival_note': '绿色棚顶', 'arrival_image': '/media/merchants/scene.jpg',
            'location_draft_address': '待核验的新位置'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['location_draft_address'], '待核验的新位置')
        self.stall.refresh_from_db()
        self.assertEqual(self.stall.image, old_image)
        self.assertEqual(self.stall.location.address, old_location)
        self.assertTrue(self.stall.transaction_enabled)
        public = self.api.get(f'/api/v1/stalls/{self.stall.pk}').data
        self.assertEqual(public['arrival_note'], '绿色棚顶')
        self.assertNotIn('location_draft_address', public)

    def test_busy_switch_blocks_new_orders_but_keeps_old_order_and_location_status(self):
        order, _ = create_order(self.student, payload(self.stall, self.product))
        confirmed = self.stall.current_session.last_confirmed_at
        response = self.api.patch(f'/api/v1/merchant/stalls/{self.stall.pk}/profile', {'accepting_orders': False}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'open')
        self.assertFalse(response.data['can_order'])
        self.assertIn('忙碌', response.data['order_unavailable_reason'])
        self.stall.refresh_from_db()
        self.assertEqual(self.stall.current_session.last_confirmed_at, confirmed)
        self.assertTrue(self.stall.can_order(existing_order=True))
        with self.assertRaises(BusinessError): create_order(self.other, payload(self.stall, self.product))
        for action in ('accept', 'ready', 'confirm_payment', 'complete'):
            merchant_action(order.pk, self.vendor, action, order.pickup_code)
        order.refresh_from_db()
        self.assertEqual(order.status, 'completed')

    def test_pause_and_close_do_not_confirm_location_implicitly(self):
        confirmed = timezone.now()-timedelta(minutes=20)
        BusinessSession.objects.filter(pk=self.stall.current_session_id).update(last_confirmed_at=confirmed)
        for status in ('paused', 'closed'):
            response = self.api.post(f'/api/v1/merchant/stalls/{self.stall.pk}/status', {'status': status}, format='json')
            self.assertEqual(response.status_code, 200)
            self.stall.current_session.refresh_from_db()
            self.assertEqual(self.stall.current_session.last_confirmed_at, confirmed)

    @override_settings(PUBLIC_BASE_URL='https://campus.example')
    def test_freshness_sort_keeps_walk_in_stalls_and_exposes_operator_stale_threshold(self):
        SiteConfiguration.objects.create(stale_minutes=15)
        fresh = Stall.objects.create(merchant=self.stall.merchant, area=self.stall.area, name='刚确认的线下摊', category='小吃')
        StallLocation.objects.create(stall=fresh, address='东门', latitude=31.23, longitude=121.47)
        fresh.current_session = BusinessSession.objects.create(stall=fresh, status='open')
        fresh.save()
        BusinessSession.objects.filter(pk=self.stall.current_session_id).update(last_confirmed_at=timezone.now()-timedelta(minutes=16))
        rows = self.api.get('/api/v1/stalls', {'sort': 'recommended'}).data['results']
        self.assertEqual([row['id'] for row in rows], [fresh.pk, self.stall.pk])
        self.assertFalse(rows[0]['can_order'])
        self.assertEqual(rows[1]['status'], 'stale')
        self.assertEqual(self.api.get('/api/v1/stalls', {'status': 'orderable'}).data['results'], [])
        config = self.api.get('/api/v1/config').data
        self.assertEqual((config['stale_minutes'], config['public_base_url']), (15, 'https://campus.example'))

    def test_application_is_owned_draft_and_submit_validates_required_fields(self):
        self.api.force_authenticate(self.student)
        self.assertIsNone(self.api.get('/api/v1/merchant/application').data['application'])
        saved = self.api.patch('/api/v1/merchant/application', {'stall_name': '我的小摊'}, format='json')
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.data['application']['source'], 'self')
        response = self.submit()
        self.assertEqual(response.status_code, 400)
        self.assertIn('contact_phone', response.data['errors'])
        self.api.force_authenticate(self.other)
        self.assertIsNone(self.api.get('/api/v1/merchant/application').data['application'])
        self.assertEqual(self.api.post('/api/v1/merchant/application/submit', {}, format='json').status_code, 404)

    def test_application_rejects_client_approval_or_identity_and_submitted_edits(self):
        self.api.force_authenticate(self.student)
        for field, value in [('status', 'approved'), ('source', 'assisted'), ('user_id', self.other.pk), ('transaction_enabled', True)]:
            self.assertEqual(self.api.patch('/api/v1/merchant/application', {field: value}, format='json').status_code, 400)
        self.api.patch('/api/v1/merchant/application', self.application_values(), format='json')
        response = self.submit()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.submit().data['application'], response.data['application'])
        self.assertEqual(self.api.patch('/api/v1/merchant/application', {'stall_name': '偷改资料'}, format='json').status_code, 409)
        self.assertEqual(self.api.get('/api/v1/auth/me').json()['merchant_application_status'], 'submitted')

    def test_assisted_application_requires_owner_confirmation_before_approval(self):
        request = RequestFactory().get('/admin/')
        request.user = self.staff
        model_admin = ApplicationAdmin(MerchantApplication, admin.site)
        draft = MerchantApplication(user=self.student, **self.application_values())
        model_admin.save_model(request, draft, type('Form', (), {'changed_data': []})(), False)
        self.assertEqual(draft.source, 'assisted')
        with self.assertRaises(BusinessError): review_application(draft.pk, self.staff, 'approved')
        self.assertEqual(self.submit().status_code, 200)
        self.assertIsNotNone(MerchantApplication.objects.get(pk=draft.pk).confirmed_at)

    def test_approval_is_atomic_idempotent_and_never_grants_trading_visibility_or_location(self):
        draft = self.application()
        self.submit()
        with self.assertRaises(BusinessError): review_application(draft.pk, self.vendor, 'approved')
        result = review_application(draft.pk, self.staff, 'approved')
        repeated = review_application(draft.pk, self.staff, 'approved')
        self.assertEqual(result.approved_stall_id, repeated.approved_stall_id)
        created = result.approved_stall
        self.assertFalse(created.is_visible)
        self.assertFalse(created.transaction_enabled)
        self.assertFalse(created.merchant.is_verified)
        self.assertFalse(created.delivery_approved)
        self.assertFalse(created.delivery_enabled)
        self.assertFalse(created.simulation_payment_enabled)
        self.assertFalse(created.simulation_delivery_enabled)
        self.assertIsNone(created.merchant.wechat_pay_account)
        self.assertFalse(StallLocation.objects.filter(stall=created).exists())
        self.assertEqual(created.location_draft_address, draft.address_note)
        self.assertEqual(MerchantProfile.objects.filter(user=self.student).count(), 1)

    def test_request_changes_and_resubmission_preserve_source_and_require_fresh_confirmation(self):
        draft = self.application('assisted')
        self.submit()
        with self.assertRaises(BusinessError): review_application(draft.pk, self.staff, 'needs_changes')
        MerchantApplication.objects.filter(pk=draft.pk).update(review_note='请补充清楚的门口地标')
        review_application(draft.pk, self.staff, 'needs_changes')
        saved = self.api.patch('/api/v1/merchant/application', {'address_note': '东门红色雨棚'}, format='json')
        self.assertEqual(saved.status_code, 200)
        self.assertIsNone(saved.data['application']['confirmed_at'])
        self.assertEqual(saved.data['application']['source'], 'assisted')
        self.assertEqual(self.submit().data['application']['status'], 'submitted')

    def test_stale_admin_draft_cannot_overwrite_owner_submission(self):
        draft = self.application('assisted')
        self.submit()
        draft.stall_name = '旧后台页面改名'
        request = RequestFactory().get('/admin/')
        request.user = self.staff
        ApplicationAdmin(MerchantApplication, admin.site).save_model(request, draft,
            type('Form', (), {'changed_data': ['stall_name']})(), True)
        draft.refresh_from_db()
        self.assertEqual(draft.status, 'submitted')
        self.assertEqual(draft.stall_name, self.application_values()['stall_name'])
        self.assertIsNotNone(draft.confirmed_at)

    def test_approval_failure_rolls_back_profile_and_status(self):
        draft = self.application()
        self.submit()
        with patch('market.operations.Stall.objects.create', side_effect=RuntimeError('injected stall write failure')):
            with self.assertRaises(RuntimeError): review_application(draft.pk, self.staff, 'approved')
        draft.refresh_from_db()
        self.assertEqual(draft.status, 'submitted')
        self.assertFalse(MerchantProfile.objects.filter(user=self.student).exists())

    def test_view_only_staff_cannot_see_or_execute_application_review_actions(self):
        viewer = User.objects.create_user('application_viewer', is_staff=True)
        viewer.user_permissions.add(Permission.objects.get(content_type__app_label='market', codename='view_merchantapplication'))
        request = RequestFactory().get('/admin/market/merchantapplication/')
        request.user = viewer
        model_admin = ApplicationAdmin(MerchantApplication, admin.site)
        self.assertTrue(model_admin.has_view_permission(request))
        self.assertFalse(model_admin.has_change_permission(request))
        self.assertFalse({'approve', 'needs_changes', 'reject'}.intersection(model_admin.get_actions(request)))
        draft = self.application()
        self.submit()
        for decision in ('approved', 'needs_changes', 'rejected'):
            with self.assertRaises(BusinessError) as denied:
                review_application(draft.pk, viewer, decision)
            self.assertEqual(denied.exception.status_code, 403)
        draft.refresh_from_db()
        self.assertEqual(draft.status, 'submitted')
        self.assertFalse(MerchantProfile.objects.filter(user=self.student).exists())
        request.user = self.staff
        self.assertTrue({'approve', 'needs_changes', 'reject'}.issubset(model_admin.get_actions(request)))

    def test_restock_replay_survives_product_deletion_without_adding_other_stock(self):
        remaining = Product.objects.create(stall=self.stall, name='保留的商品', price_cents=500, stock=6)
        key = uuid.uuid4().hex
        items = [{'product_id': self.product.pk, 'quantity': 3}, {'product_id': remaining.pk, 'quantity': 2}]
        first = self.restock(items, key)
        self.assertEqual(first.status_code, 200, first.data)
        self.product.delete()
        replay = self.restock(items, key)
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertTrue(replay.data['replayed'])
        self.assertEqual([row['id'] for row in replay.data['products']], [remaining.pk])
        remaining.refresh_from_db()
        self.assertEqual(remaining.stock, 8)
        self.assertEqual(RestockBatch.objects.count(), 1)
        remaining.delete()
        replay = self.restock(items, key)
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertEqual(replay.data, {'products': [], 'replayed': True})
        # A new operation still rejects missing items, as does a changed payload.
        self.assertEqual(self.restock(items, uuid.uuid4().hex).status_code, 400)
        changed = [{**items[0], 'quantity': 4}, items[1]]
        self.assertEqual(self.restock(changed, key).status_code, 409)

    def test_restock_is_incremental_idempotent_and_payload_sensitive(self):
        key = uuid.uuid4().hex
        response = self.restock(key=key)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data['replayed'])
        create_order(self.student, payload(self.stall, self.product))
        response = self.restock(key=key)
        self.assertTrue(response.data['replayed'])
        self.assertEqual(response.data['products'][0]['stock'], 7)
        conflict = self.restock([{'product_id': self.product.pk, 'quantity': 4}], key)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(RestockBatch.objects.count(), 1)

    def test_restock_batch_validation_is_atomic_and_does_not_activate_products(self):
        self.product.is_active = False
        self.product.save()
        response = self.restock([{'product_id': self.product.pk, 'quantity': 2}, {'product_id': 99999, 'quantity': 2}])
        self.assertEqual(response.status_code, 400)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 5)
        self.assertEqual(RestockBatch.objects.count(), 0)
        self.assertEqual(self.restock().status_code, 200)
        self.product.refresh_from_db()
        self.assertFalse(self.product.is_active)
        for quantity in (0, -1, 100001):
            self.assertEqual(self.restock([{'product_id': self.product.pk, 'quantity': quantity}]).status_code, 400)

    def test_restock_permissions_and_cancel_release_are_preserved(self):
        self.api.force_authenticate(self.other)
        self.assertEqual(self.restock().status_code, 404)
        self.api.force_authenticate(self.vendor)
        order, _ = create_order(self.student, payload(self.stall, self.product, quantity=2))
        self.restock()
        cancel_order(order.pk, self.student, '不需要了')
        cancel_order(order.pk, self.student, '重复取消')
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 8)

    def test_guest_location_report_is_idempotent_and_never_changes_stall_status(self):
        self.api.force_authenticate(None)
        values = self.report_values()
        first = self.api.post('/api/v1/feedback', values, format='json')
        self.assertEqual(first.status_code, 201, first.data)
        second = self.api.post('/api/v1/feedback', values, format='json')
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data['id'], second.data['id'])
        self.assertTrue(second.data['replayed'])
        self.assertEqual(Feedback.objects.count(), 1)
        values['kind'] = 'wrong_location'
        self.assertEqual(self.api.post('/api/v1/feedback', values, format='json').status_code, 409)
        self.stall.refresh_from_db()
        self.assertEqual(self.stall.effective_status(), 'open')

    def test_location_report_privacy_ownership_and_confirmation_does_not_resolve(self):
        self.api.force_authenticate(self.student)
        self.api.post('/api/v1/feedback', self.report_values(), format='json')
        self.api.force_authenticate(self.vendor)
        response = self.api.get(f'/api/v1/merchant/stalls/{self.stall.pk}/location-reports')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['unresolved_count'], 1)
        report = response.data['reports'][0]
        self.assertEqual(report['snapshot_source'], 'user_reported_display')
        self.assertFalse({'user', 'contact', 'content', 'user_id'}.intersection(report))
        self.assertNotIn('13900001234', str(response.data))
        self.api.post(f'/api/v1/merchant/stalls/{self.stall.pk}/status', {'status': 'open', 'confirm_location': True}, format='json')
        self.assertFalse(Feedback.objects.first().resolved)
        self.assertEqual(self.api.post(f'/api/v1/merchant/stalls/{self.stall.pk}/location-reports', {'resolved': True}, format='json').status_code, 405)
        self.api.force_authenticate(self.other)
        self.assertEqual(self.api.get(f'/api/v1/merchant/stalls/{self.stall.pk}/location-reports').status_code, 404)

    def test_location_report_rejects_private_extra_fields_and_invalid_snapshot(self):
        for extra in ({'student_id': 10}, {'latitude': float('inf')}, {'last_confirmed_at': 'not-a-time'}):
            values = self.report_values()
            values['location_snapshot'].update(extra)
            # Infinity is deliberately sent as raw JSON for parser/validation rejection.
            if 'latitude' in extra:
                response = self.api.generic('POST', '/api/v1/feedback',
                    '{"kind":"not_found","location_snapshot":{"latitude":1e999}}', content_type='application/json')
            else: response = self.api.post('/api/v1/feedback', values, format='json')
            self.assertEqual(response.status_code, 400)
        values = self.report_values()
        values['resolved'] = True
        self.assertEqual(self.api.post('/api/v1/feedback', values, format='json').status_code, 400)
        self.assertEqual(Feedback.objects.count(), 0)

    def test_feedback_csrf_throttling_and_legacy_general_feedback(self):
        guest = APIClient(enforce_csrf_checks=True)
        self.assertEqual(guest.post('/api/v1/feedback', self.report_values(), format='json').status_code, 403)
        cache.clear()  # A rejected CSRF request legitimately consumes an attempt.
        self.api.force_authenticate(None)
        for number in range(20):
            response = self.api.post('/api/v1/feedback', {'content': f'建议 {number}'}, format='json')
            self.assertEqual(response.status_code, 201)
        self.assertEqual(self.api.post('/api/v1/feedback', {'content': '过于频繁'}, format='json').status_code, 429)

    def test_events_accept_only_enumerated_non_identifying_sources(self):
        for kind in ('share_click', 'share_open', 'qr_open', 'route_click', 'reorder'):
            response = self.api.post('/api/v1/events', {'type': kind, 'stall_id': self.stall.pk, 'source': 'home_recent'}, format='json')
            self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Event.objects.count(), 5)
        self.assertEqual(Event.objects.first().metadata, {'source': 'home_recent'})
        for body in ({'source': 'https://private.example/user'}, {'metadata': {'phone': '13800138000'}},
                     {'source': 'map', 'metadata': {'source': 'home'}}):
            self.assertEqual(self.api.post('/api/v1/events', {'type': 'stall_view', **body}, format='json').status_code, 400)

    def test_recent_completed_returns_only_owners_latest_three_full_orders(self):
        for index in range(4): self.historical_order(completed_at=timezone.now()-timedelta(days=index))
        self.historical_order(user=self.other)
        self.historical_order(status='cancelled')
        self.api.force_authenticate(self.student)
        response = self.api.get('/api/v1/orders/recent-completed')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 3)
        self.assertTrue(all(row['status'] == 'completed' for row in response.data))
        self.assertTrue(all('items' in row and 'pickup_code' in row for row in response.data))
        expected = list(Order.objects.filter(user=self.student, status='completed').order_by('-completed_at')[:3])
        self.assertEqual([row['id'] for row in response.data], [str(order.pk) for order in expected])
        self.api.force_authenticate(None)
        self.assertEqual(self.api.get('/api/v1/orders/recent-completed').status_code, 403)

    def test_returning_customer_metrics_are_mode_scoped_and_deduplicated(self):
        self.historical_order(completed_at=timezone.now()-timedelta(days=40))
        self.historical_order()
        self.historical_order()
        self.historical_order(user=self.other, mode='simulation', completed_at=timezone.now()-timedelta(days=40))
        self.historical_order(user=self.other)
        response = self.api.get('/api/v1/merchant/metrics', {'stall': self.stall.pk, 'days': 7, 'mode': 'live'})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['completed_customer_count'], 2)
        self.assertEqual(response.data['returning_customer_count'], 1)
        self.assertEqual(response.data['returning_customer_rate'], 0.5)
        empty = self.api.get('/api/v1/merchant/metrics', {'stall': self.stall.pk, 'days': 7, 'mode': 'simulation'})
        self.assertIsNone(empty.data['returning_customer_rate'])

    def test_source_metrics_aggregate_only_owned_whitelisted_events(self):
        for _ in range(2): Event.objects.create(type='qr_open', stall=self.stall, metadata={'source': 'stall_qr'})
        Event.objects.create(type='route_click', stall=self.stall, metadata={'source': 'map'})
        Event.objects.create(type='qr_open', stall=self.stall, metadata={'source': 'untrusted-private-url'})
        Event.objects.create(type='qr_open', stall=None, metadata={'source': 'stall_qr'})
        response = self.api.get('/api/v1/merchant/metrics', {'stall': self.stall.pk, 'days': 7})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['source_counts'], [
            {'source': 'stall_qr', 'event_type': 'qr_open', 'count': 2},
            {'source': 'map', 'event_type': 'route_click', 'count': 1}])
        self.assertIn('不能解释为真实扫码人数', response.data['metric_definitions']['source_counts'])

    @override_settings(SERVICES_SIMULATION_ENABLED=True)
    def test_busy_switch_does_not_refund_previously_reserved_delivery_payment(self):
        from .simulation import prepare_stall
        self.stall.is_demo = True
        self.stall.simulation_payment_enabled = True
        self.stall.simulation_delivery_enabled = True
        self.stall.save()
        point = prepare_stall(self.stall)
        data = {**payload(self.stall, self.product), 'fulfillment_type': 'delivery',
                'delivery_point_id': point.pk, 'expected_delivery_fee_cents': 200,
                'recipient_name': '同学', 'contact_phone': '13800138000'}
        order, _ = create_order(self.student, data)
        self.api.patch(f'/api/v1/merchant/stalls/{self.stall.pk}/profile', {'accepting_orders': False}, format='json')
        self.api.force_authenticate(self.student)
        started = self.api.post(f'/api/v1/orders/{order.pk}/payments/wechat', {'channel': 'simulation'}, format='json')
        self.assertEqual(started.status_code, 200, started.data)
        paid = self.api.post(f'/api/v1/orders/{order.pk}/payments/simulate', {
            'payment_id': started.data['payment']['id'], 'outcome': 'success'}, format='json')
        self.assertEqual(paid.status_code, 200, paid.data)
        self.assertEqual(paid.data['status'], 'pending')
        self.assertEqual(paid.data['payment_status'], 'paid')
        self.assertIsNone(paid.data['refund'])


@skipUnless(connection.vendor == 'postgresql', 'Row-lock races require PostgreSQL')
class OperationsConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.student, self.other, self.vendor, self.stall, self.product = fixtures()

    def race(self, first, second):
        gate = Barrier(2)
        def run(task):
            close_old_connections()
            try:
                gate.wait(timeout=10)
                return task()
            finally: connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = pool.submit(run, first), pool.submit(run, second)
            return a.result(timeout=30), b.result(timeout=30)

    def restock_request(self, key):
        client = APIClient()
        client.force_authenticate(User.objects.get(pk=self.vendor.pk))
        result = client.post(f'/api/v1/merchant/stalls/{self.stall.pk}/restock', {
            'idempotency_key': key, 'items': [{'product_id': self.product.pk, 'quantity': 3}]}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        return result.data

    def test_restock_racing_checkout_keeps_sum(self):
        self.race(lambda: self.restock_request(uuid.uuid4().hex),
            lambda: create_order(User.objects.get(pk=self.student.pk), payload(self.stall, self.product, quantity=2)))
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 6)

    def test_duplicate_restock_concurrently_adds_once(self):
        key = uuid.uuid4().hex
        a, b = self.race(lambda: self.restock_request(key), lambda: self.restock_request(key))
        self.assertEqual(sorted([a['replayed'], b['replayed']]), [False, True])
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 8)
        self.assertEqual(RestockBatch.objects.count(), 1)

    def test_restock_racing_cancel_releases_reserved_units_once(self):
        order, _ = create_order(self.student, payload(self.stall, self.product, quantity=2))
        self.race(lambda: self.restock_request(uuid.uuid4().hex),
            lambda: cancel_order(order.pk, User.objects.get(pk=self.student.pk), '取消'))
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 8)

    def test_duplicate_approval_creates_one_hidden_stall(self):
        staff = User.objects.create_user('reviewer', is_staff=True)
        staff.user_permissions.add(Permission.objects.get(content_type__app_label='market', codename='change_merchantapplication'))
        application = MerchantApplication.objects.create(user=self.student, status='submitted', confirmed_at=timezone.now(),
            business_name='经营主体', stall_name='饭摊', contact_phone='13800138000', area=self.stall.area,
            category='正餐', address_note='门口')
        a, b = self.race(lambda: review_application(application.pk, staff, 'approved'),
                        lambda: review_application(application.pk, staff, 'approved'))
        self.assertEqual(a.approved_stall_id, b.approved_stall_id)
        self.assertEqual(Stall.objects.filter(merchant__user=self.student).count(), 1)
