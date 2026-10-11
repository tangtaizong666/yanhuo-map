"""Location consent and public discovery must survive stale client state."""
from unittest.mock import patch
import uuid

from django.test import TestCase, override_settings
from django.contrib.auth.models import Permission, User
from django.utils import timezone
from rest_framework.test import APIClient

from . import discovery_api
from .models import Order, Product, Review, Stall
from .serializers import StallSerializer
from .services import create_order
from .tests import fixtures, payload


@override_settings(DEMO_MODE=False)
class CheckoutLocationContractTests(TestCase):
    def setUp(self):
        self.student, self.other, self.vendor, self.stall, self.product = fixtures()
        self.client = APIClient()
        self.client.force_authenticate(self.student)

    def detail(self):
        return self.client.get(f'/api/v1/stalls/{self.stall.pk}').data

    def move_and_reapprove(self):
        self.client.force_authenticate(self.vendor)
        moved = self.client.post(f'/api/v1/merchant/stalls/{self.stall.pk}/status', {
            'status': 'open', 'confirm_location': True, 'address': '校园北门',
            'latitude': 31.24, 'longitude': 121.48,
        }, format='json')
        self.assertEqual(moved.status_code, 200, moved.data)
        self.assertFalse(Stall.objects.get(pk=self.stall.pk).transaction_enabled)
        Stall.objects.filter(pk=self.stall.pk).update(transaction_enabled=True)
        self.client.force_authenticate(self.student)

    def test_stale_location_rejected_before_stock_reservation(self):
        data = payload(self.stall, self.product)
        data['expected_location_token'] = self.detail().get('location_token', '0' * 64)
        self.move_and_reapprove()
        response = self.client.post('/api/v1/orders', data, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'location_changed')
        self.assertEqual(response.data['current_location']['address'], '校园北门')
        self.assertFalse(Order.objects.exists())
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 5)
        data['expected_location_token'] = self.detail()['location_token']
        created = self.client.post('/api/v1/orders', data, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data['pickup_address'], '校园北门')

    def test_successful_retry_preserves_original_order_after_move(self):
        data = {**payload(self.stall, self.product),
            'expected_location_token': self.detail().get('location_token', '0' * 64)}
        created = self.client.post('/api/v1/orders', data, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        self.move_and_reapprove()
        replay = self.client.post('/api/v1/orders', data, format='json')
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertEqual(replay.data['id'], created.data['id'])
        self.assertEqual(replay.data['pickup_address'], '校园南门')
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 4)
        data['expected_location_token'] = self.detail()['location_token']
        changed = self.client.post('/api/v1/orders', data, format='json')
        self.assertEqual(changed.data['code'], 'idempotency_conflict')

    def test_position_confirmation_does_not_invalidate_location_token(self):
        before = self.detail()
        self.assertRegex(before.get('location_token', ''), r'^[0-9a-f]{64}$')
        self.stall.current_session.last_confirmed_at = timezone.now()
        self.stall.current_session.save(update_fields=['last_confirmed_at'])
        self.assertEqual(before['location_token'], self.detail()['location_token'])

    def test_legacy_request_without_token_stays_compatible(self):
        response = self.client.post('/api/v1/orders', payload(self.stall, self.product), format='json')
        self.assertEqual(response.status_code, 201, response.data)

    def test_each_location_component_changes_consent_but_a_different_stall_cannot_reuse_it(self):
        original = self.detail()['location_token']
        location = self.stall.location
        for field, value in [('address', '校园东门'), ('latitude', 31.2301), ('longitude', 121.4701)]:
            before = getattr(location, field)
            setattr(location, field, value)
            location.save(update_fields=[field])
            self.assertNotEqual(self.detail()['location_token'], original, field)
            setattr(location, field, before)
            location.save(update_fields=[field])
        from .location_contract import location_snapshot
        self.stall.pk += 100
        self.assertNotEqual(location_snapshot(self.stall)['location_token'], original)

    def test_malformed_token_is_a_validation_error(self):
        response = self.client.post('/api/v1/orders', {
            **payload(self.stall, self.product), 'expected_location_token': 'not-a-token'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(Order.objects.exists())


@override_settings(DEMO_MODE=False)
class DiscoveryVisibilityContractTests(TestCase):
    def test_stall_hidden_after_product_page_is_not_returned_or_a_server_error(self):
        _, _, _, stall, _ = fixtures()
        original = discovery_api.page

        def hide_after_page(*args, **kwargs):
            result = original(*args, **kwargs)
            Stall.objects.filter(pk=stall.pk).update(is_visible=False)
            return result

        with patch('market.discovery_api.page', side_effect=hide_after_page):
            response = APIClient().get('/api/v1/products')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['results'], [])


@override_settings(DEMO_MODE=False)
class ReviewModeContractTests(TestCase):
    def setUp(self):
        self.student, _, self.vendor, self.stall, self.product = fixtures()
        self.client = APIClient()
        self.client.force_authenticate(self.student)
        self.live = self.add_review('live', 2, '真实体验')
        self.simulated = self.add_review('simulation', 5, '仅供演练')

    def add_review(self, mode, rating, content):
        order, _ = create_order(self.student, payload(self.stall, self.product))
        Order.objects.filter(pk=order.pk).update(mode=mode, status='completed')
        response = self.client.post(f'/api/v1/orders/{order.pk}/review',
            {'rating': rating, 'content': content}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return Review.objects.get(order=order)

    def test_public_detail_and_summary_exclude_simulation_feedback_and_ratings(self):
        detail = self.client.get(f'/api/v1/stalls/{self.stall.pk}').data
        summary = self.client.get('/api/v1/stalls').data['results'][0]
        for result in (detail, summary):
            self.assertEqual(result['rating'], 2.0)
            self.assertEqual(result['review_count'], 1)
        self.assertEqual([row['id'] for row in detail['reviews']], [self.live.pk])
        self.assertEqual(detail['reviews'][0]['mode'], 'live')
        self.assertEqual(detail['order_count'], 1)

    def test_unannotated_stall_serializer_uses_the_same_public_review_policy(self):
        result = StallSerializer(Stall.objects.get(pk=self.stall.pk)).data
        self.assertEqual(result['rating'], 2.0)
        self.assertEqual(result['review_count'], 1)
        self.assertEqual([row['id'] for row in result['reviews']], [self.live.pk])

    def test_merchant_reviews_default_live_and_simulation_requires_explicit_mode(self):
        self.client.force_authenticate(self.vendor)
        live = self.client.get(f'/api/v1/merchant/reviews?stall={self.stall.pk}')
        simulation = self.client.get(f'/api/v1/merchant/reviews?stall={self.stall.pk}&mode=simulation')
        self.assertEqual([row['id'] for row in live.data], [self.live.pk])
        self.assertEqual([row['id'] for row in simulation.data], [self.simulated.pk])
        self.assertEqual(simulation.data[0]['mode'], 'simulation')
        self.assertEqual(self.client.get('/api/v1/merchant/reviews?mode=unknown').status_code, 400)

    def test_simulated_order_keeps_its_own_review(self):
        detail = self.client.get(f'/api/v1/orders/{self.simulated.order_id}').data
        self.assertEqual(detail['review']['id'], self.simulated.pk)
        self.assertEqual(detail['review'].get('mode'), 'simulation')

    def test_simulated_reviews_do_not_displace_real_feedback_from_twenty_review_preview(self):
        for _ in range(21):
            order = Order.objects.get(pk=self.simulated.order_id)
            order.pk, order.number, order.idempotency_key = uuid.uuid4(), uuid.uuid4().hex, uuid.uuid4().hex
            order.save(force_insert=True)
            Review.objects.create(order=order, user=self.student, stall=self.stall, rating=5, content='模拟')
        detail = self.client.get(f'/api/v1/stalls/{self.stall.pk}').data
        self.assertEqual([row['id'] for row in detail['reviews']], [self.live.pk])
        self.assertEqual(detail['review_count'], 1)


@override_settings(DEMO_MODE=False)
class MerchantOrderDetailContractTests(TestCase):
    def setUp(self):
        self.student, self.other, self.vendor, self.stall, self.product = fixtures()
        self.order, _ = create_order(self.student, payload(self.stall, self.product))
        Order.objects.filter(pk=self.order.pk).update(status='ready', payment_status='paid')
        self.url = f'/api/v1/merchant/orders/{self.order.pk}'
        self.client = APIClient()

    def test_owner_can_refresh_order_without_customer_pickup_code(self):
        self.client.force_authenticate(self.vendor)
        response = self.client.get(self.url, {'stall': self.stall.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['pickup_code'], '')
        self.assertIn('complete', response.data['allowed_actions'])
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        Order.objects.filter(pk=self.order.pk).update(status='completed')
        self.assertEqual(self.client.get(self.url).data['status'], 'completed')
        self.assertEqual(self.client.get(self.url, {'stall': self.stall.pk + 1}).status_code, 404)
        self.assertEqual(self.client.get(self.url, {'stall': 'invalid'}).status_code, 400)

    def test_other_accounts_cannot_read_merchant_order(self):
        self.assertEqual(self.client.get(self.url).status_code, 403)
        for user in (self.student, self.other, User.objects.create_user('unprivileged_staff', is_staff=True)):
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_operator_needs_explicit_read_permission_and_receives_no_write_actions(self):
        reader = User.objects.create_user('order_reader', is_staff=True)
        reader.user_permissions.add(Permission.objects.get(content_type__app_label='market', codename='view_order'))
        self.client.force_authenticate(reader)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['pickup_code'], '')
        self.assertEqual(response.data['allowed_actions'], [])
