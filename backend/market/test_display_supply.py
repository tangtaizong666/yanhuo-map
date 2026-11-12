from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Product
from .tests import fixtures, payload


@override_settings(DEMO_MODE=False, SERVICES_SIMULATION_ENABLED=False)
class DisplaySupplyTests(TestCase):
    def setUp(self):
        self.student, self.other, self.vendor, self.stall, self.product = fixtures()
        self.stall.merchant.qualification_tier = 'mobile_vendor'
        self.stall.merchant.save(update_fields=['qualification_tier'])
        self.client = APIClient()
        self.client.force_authenticate(self.vendor)

    def detail_product(self, product_id=None):
        response = self.client.get(f'/api/v1/stalls/{self.stall.pk}')
        self.assertEqual(response.status_code, 200, response.data)
        return next(p for p in response.data['products'] if p['id'] == (product_id or self.product.pk))

    def test_create_display_dish_without_inventory_and_retry(self):
        body = {'name': '今天的烤饼', 'price_cents': 500, 'display_availability': 'available',
            'idempotency_key': 'display-create-1'}
        url = f'/api/v1/merchant/stalls/{self.stall.pk}/products'
        response = self.client.post(url, body, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['stock'], 0)
        again = self.client.post(url, body, format='json')
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.data['id'], response.data['id'])
        dish = self.detail_product(response.data['id'])
        self.assertEqual((dish['availability'], dish['max_order_quantity'], dish['display_only']), ('available', 0, True))
        self.assertNotIn('stock', dish)
        self.client.force_authenticate(self.student)
        rejected = self.client.post('/api/v1/orders', payload(self.stall, Product.objects.get(pk=dish['id'])), format='json')
        self.assertEqual(rejected.status_code, 409)

    def test_three_states_do_not_change_reserved_inventory_or_manual_pause(self):
        self.product.sale_paused = True
        self.product.save(update_fields=['sale_paused'])
        for state in ('available', 'sold_out', 'paused'):
            with self.subTest(state=state):
                response = self.client.patch(f'/api/v1/merchant/products/{self.product.pk}',
                    {'display_availability': state}, format='json')
                self.assertEqual(response.status_code, 200, response.data)
                self.product.refresh_from_db()
                self.assertEqual((self.product.stock, self.product.stock_version, self.product.sale_paused), (5, 0, True))
                public = self.detail_product()
                self.assertEqual(public['availability'], state)
                self.assertEqual(public['sale_paused'], state == 'paused')

    def test_discovery_and_preview_match_display_state_with_zero_stock(self):
        self.product.stock = 0
        self.product.display_availability = 'available'
        self.product.save(update_fields=['stock', 'display_availability'])
        for state, expected in [('available', True), ('sold_out', False), ('paused', False)]:
            self.product.display_availability = state
            self.product.save(update_fields=['display_availability'])
            dishes = self.client.get('/api/v1/products').data['results']
            summaries = self.client.get('/api/v1/stalls').data['results']
            self.assertEqual(any(row['product']['id'] == self.product.pk for row in dishes), expected)
            self.assertEqual(any(row['id'] == self.product.pk for row in summaries[0]['products']), expected)
        self.product.display_availability = 'available'
        self.product.save(update_fields=['display_availability'])
        self.stall.current_session.last_confirmed_at = timezone.now() - timedelta(hours=2)
        self.stall.current_session.save(update_fields=['last_confirmed_at'])
        self.assertEqual(self.client.get('/api/v1/products').data['results'], [])

    def test_returning_to_trade_uses_stock_and_preserves_manual_pause(self):
        self.product.display_availability = 'available'
        self.product.stock = 0
        self.product.save(update_fields=['display_availability', 'stock'])
        merchant = self.stall.merchant
        merchant.qualification_tier = 'storefront'
        merchant.save(update_fields=['qualification_tier'])
        dish = self.detail_product()
        self.assertEqual((dish['availability'], dish['display_only']), ('sold_out', False))
        self.product.stock, self.product.sale_paused = 10, True
        self.product.save(update_fields=['stock', 'sale_paused'])
        self.assertEqual(self.detail_product()['availability'], 'paused')

    def test_supply_update_rejects_other_account_and_invalid_state(self):
        url = f'/api/v1/merchant/products/{self.product.pk}'
        self.assertEqual(self.client.patch(url, {'display_availability': 'invented'}, format='json').status_code, 400)
        self.client.force_authenticate(self.other)
        self.assertIn(self.client.patch(url, {'display_availability': 'sold_out'}, format='json').status_code, (403, 404))
        self.product.refresh_from_db()
        self.assertEqual(self.product.display_availability, '')

    def test_legacy_empty_state_preserves_existing_availability(self):
        self.assertEqual(self.detail_product()['availability'], 'available')
        self.product.stock = 0
        self.product.save(update_fields=['stock'])
        self.assertEqual(self.detail_product()['availability'], 'sold_out')
