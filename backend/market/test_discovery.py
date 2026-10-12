from datetime import timedelta
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.utils import timezone
from rest_framework.test import APIClient

from .models import BusinessSession, Follow, Product, Stall, StallLocation
from .tests import fixtures


@override_settings(DEMO_MODE=True)
class DiscoveryContractTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.student, cls.other, cls.vendor, cls.stall, cls.product = fixtures()

    def setUp(self):
        self.api = APIClient()

    def add_stall(self, index):
        stall = Stall.objects.create(merchant=self.stall.merchant, area=self.stall.area,
            name=f'摊位 {index}', category='小吃', transaction_enabled=True)
        StallLocation.objects.create(stall=stall, address='校园东门', latitude=31 + index / 100000, longitude=121)
        stall.current_session = BusinessSession.objects.create(stall=stall, status='open', last_confirmed_at=timezone.now())
        stall.save(update_fields=['current_session'])
        return stall

    def test_summary_bounded_and_cursor_scoped_without_full_menu(self):
        for index in range(24): self.add_stall(index)
        for index in range(6): Product.objects.create(stall=self.stall, name=f'餐点 {index}', price_cents=600, stock=100)
        first = self.api.get('/api/v1/stalls')
        self.assertEqual(first.status_code, 200)
        self.assertEqual(len(first.data['results']), 20)
        self.assertTrue(first.data['next'])
        all_ids = {row['id'] for row in first.data['results']}
        second = self.api.get('/api/v1/stalls', {'cursor': first.data['next']})
        self.assertEqual(len(second.data['results']), 5)
        self.assertFalse(all_ids & {row['id'] for row in second.data['results']})
        self.assertIsNone(second.data['next'])
        for row in first.data['results'] + second.data['results']:
            self.assertLessEqual(len(row['products']), 2)
            for key in ('reviews', 'wechat_payment', 'delivery', 'contact_phone', 'prep_active_orders', 'receiving_seen_at'):
                self.assertNotIn(key, row)
        self.assertEqual(self.api.get('/api/v1/stalls', {'cursor': first.data['next'], 'q': 'different'}).status_code, 400)
        self.assertEqual(self.api.get('/api/v1/stalls', {'page_size': 51}).status_code, 400)

    def test_detail_privacy_and_merchant_inventory_are_distinct(self):
        self.stall.merchant.contact_phone = '13800000000'
        self.stall.merchant.save(update_fields=['contact_phone'])
        self.stall.image = 'https://external.example/tracking.jpg'
        self.stall.save(update_fields=['image'])
        public = self.api.get(f'/api/v1/stalls/{self.stall.pk}').data
        self.assertEqual(public['contact_phone'], '')
        self.assertEqual(public['image'], '')
        self.assertEqual(public['products'][0]['availability'], 'available')
        self.assertEqual(public['products'][0]['max_order_quantity'], 10)
        for key in ('stock', 'stock_version'): self.assertNotIn(key, public['products'][0])
        for key in ('prep_capacity', 'prep_active_orders', 'receiving_seen_at', 'receiving_age_seconds'): self.assertNotIn(key, public)
        self.assertNotIn('capacity', public['delivery'])
        self.stall.public_phone_enabled = True
        self.stall.save(update_fields=['public_phone_enabled'])
        self.assertEqual(self.api.get(f'/api/v1/stalls/{self.stall.pk}').data['contact_phone'], '13800000000')
        self.api.force_authenticate(self.vendor)
        owned = self.api.get('/api/v1/merchant/stalls').data[0]
        self.assertIn('stock_version', owned['products'][0])
        self.assertEqual(owned['image'], 'https://external.example/tracking.jpg')
        self.assertIn('steps', owned['activation'])
        self.assertEqual(owned['activation']['steps'][0]['key'], 'application')
        self.assertEqual(owned['activation']['steps'][0]['status'], 'done')

    def test_product_search_reaches_beyond_preview_and_respects_budget(self):
        for index in range(3): Product.objects.create(stall=self.stall, name=f'其他菜 {index}', price_cents=600, stock=10)
        match = Product.objects.create(stall=self.stall, name='特别柠檬茶', price_cents=800, stock=2)
        response = self.api.get('/api/v1/products', {'q': '柠檬', 'budget': 800, 'meal_sort': 'price'})
        self.assertEqual([row['product']['id'] for row in response.data['results']], [match.pk])
        self.assertEqual(self.api.get('/api/v1/products', {'q': '柠檬', 'budget': 799}).data['results'], [])
        match.sale_paused = True; match.save(update_fields=['sale_paused'])
        self.assertEqual(self.api.get('/api/v1/products', {'q': '柠檬'}).data['results'], [])

    def test_map_bounds_and_overflow_are_explicit_and_lightweight(self):
        for index in range(201): self.add_stall(index)
        response = self.api.get('/api/v1/stalls/map', {'bounds': '120,30,122,32'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 200)
        self.assertTrue(response.data['truncated'])
        self.assertNotIn('products', response.data['results'][0])
        self.assertEqual(self.api.get('/api/v1/stalls/map', {'bounds': '0,0,1,1'}).data['results'], [])
        self.assertEqual(self.api.get('/api/v1/stalls/map', {'bounds': 'nan,0,1,1'}).status_code, 400)

    def test_follow_is_compact_and_follow_list_is_paginated(self):
        self.api.force_authenticate(self.student)
        response = self.api.post(f'/api/v1/stalls/{self.stall.pk}/follow')
        self.assertEqual(response.data, {'id': self.stall.pk, 'is_followed': True})
        self.assertEqual(self.api.get('/api/v1/follows').data['results'][0]['id'], self.stall.pk)
        self.api.force_authenticate(self.other)
        self.assertEqual(self.api.get('/api/v1/follows').data['results'], [])

    def test_distance_and_equal_rating_sort_are_database_stable(self):
        second = self.add_stall(1)
        for sort in ('distance', 'rating', 'freshness'):
            params = {'sort': sort, 'lat': 31, 'lng': 121, 'page_size': 1}
            first = self.api.get('/api/v1/stalls', params)
            self.assertEqual(first.status_code, 200)
            next_page = self.api.get('/api/v1/stalls', {**params, 'cursor': first.data['next']})
            self.assertEqual({first.data['results'][0]['id'], next_page.data['results'][0]['id']}, {self.stall.pk, second.pk})

    def test_unconfirmed_location_never_gains_a_fabricated_distance(self):
        unlocated = Stall.objects.create(merchant=self.stall.merchant, area=self.stall.area, name='还未确认位置')
        response = self.api.get('/api/v1/stalls', {'sort':'distance', 'lat':31, 'lng':121})
        self.assertEqual(response.data['results'][-1]['id'], unlocated.pk)
        self.assertIsNone(response.data['results'][-1]['distance_m'])

    def test_query_count_does_not_grow_with_visible_stalls(self):
        def count():
            with CaptureQueriesContext(connection) as captured:
                response = self.api.get('/api/v1/stalls', {'page_size': 50})
                self.assertEqual(response.status_code, 200)
            return len(captured)
        baseline = count()
        for index in range(9): self.add_stall(index)
        self.assertEqual(count(), baseline)
