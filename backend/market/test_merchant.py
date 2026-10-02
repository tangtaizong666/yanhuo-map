import io
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import AuditLog, Event, Follow, Order, Product, Review, StallLocation
from .services import create_order
from .tests import fixtures, payload


class MerchantWorkspaceTests(TestCase):
    def setUp(self):
        self.student, self.other, self.vendor, self.stall, self.product = fixtures()
        self.client = APIClient()
        self.client.force_authenticate(self.vendor)
        self.base = f'/api/v1/merchant/stalls/{self.stall.pk}'

    def order(self):
        return create_order(self.student, payload(self.stall, self.product))[0]

    def test_product_create_full_edit_and_public_hides_inactive(self):
        result = self.client.post(self.base + '/products', {'name': '桂花冰茶', 'category': '饮品',
            'description': '清甜桂花香', 'price_cents': 500, 'stock': 12,
            'image': '/images/tea.jpg', 'is_active': False}, format='json')
        self.assertEqual(result.status_code, 201, result.data)
        new_id = result.data['id']
        result = self.client.get('/api/v1/merchant/stalls').data[0]
        self.assertEqual(len(result['products']), 2)
        self.assertEqual(result['products'][1]['category'], '饮品')
        public = self.client.get(f'/api/v1/stalls/{self.stall.pk}').data
        self.assertEqual([p['id'] for p in public['products']], [self.product.pk])
        update = self.client.patch(f'/api/v1/merchant/products/{new_id}',
            {'name': '桂花柠檬茶', 'is_active': True, 'description': '鲜切柠檬'}, format='json')
        self.assertEqual(update.status_code, 200)
        self.assertEqual(update.data['stock'], 12)
        self.assertEqual(update.data['price_cents'], 500)
        self.assertEqual(len(self.client.get(f'/api/v1/stalls/{self.stall.pk}').data['products']), 2)

    def test_partial_price_edit_does_not_restore_reserved_stock_or_rewrite_snapshot(self):
        order = self.order()
        result = self.client.patch(f'/api/v1/merchant/products/{self.product.pk}',
            {'price_cents': 1200, 'name': '新名字', 'is_active': False}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['stock'], 4)
        item = order.items.get()
        self.assertEqual(item.name, '招牌烤冷面')
        self.assertEqual(item.unit_price_cents, 800)
        self.assertEqual(order.total_cents, 800)
        self.client.force_authenticate(self.student)
        self.assertEqual(self.client.get(f'/api/v1/orders/{order.id}').status_code, 200)
        self.assertEqual(self.client.post('/api/v1/orders', payload(self.stall, self.product), format='json').status_code, 400)

    def test_product_write_validation_and_permissions(self):
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.post(self.base + '/products', {'name': '越权商品', 'price_cents': 100}, format='json').status_code, 404)
        self.assertEqual(self.client.patch(f'/api/v1/merchant/products/{self.product.pk}', {'is_active': False}, format='json').status_code, 404)
        self.client.force_authenticate(self.vendor)
        for invalid in ({'price_cents': 0}, {'stock': -1}, {'stall_id': 99}, {'image': 'javascript:alert(1)'}, {'image': 'https://['}, [{'name': '格式错误'}], {}):
            result = self.client.patch(f'/api/v1/merchant/products/{self.product.pk}', invalid, format='json')
            self.assertEqual(result.status_code, 400, result.data)

    def test_profile_edits_preserve_historical_stall_and_qualifications(self):
        order = self.order()
        result = self.client.patch(self.base + '/profile', {'name': '校园烤冷面', 'description': '每日新鲜制作',
            'prep_minutes': 15, 'contact_phone': '13800138000', 'image': '/images/stall.jpg'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['contact_phone'], '13800138000')
        self.assertEqual(result.data['prep_minutes'], 15)
        order.refresh_from_db()
        self.assertEqual(order.stall_name, '测试烤冷面')
        self.assertEqual(order.pickup_address, '校园南门')
        result = self.client.patch(self.base + '/profile', {'transaction_enabled': True, 'is_verified': True}, format='json')
        self.assertEqual(result.status_code, 400)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.patch(self.base + '/profile', {'name': '越权'}, format='json').status_code, 404)

    def test_confirming_stale_paused_stall_preserves_pause(self):
        session = self.stall.current_session
        session.status = 'paused'
        session.last_confirmed_at = timezone.now() - timedelta(minutes=61)
        session.save()
        before = self.client.get('/api/v1/merchant/stalls').data[0]
        self.assertEqual(before['status'], 'stale')
        self.assertEqual(before['session_status'], 'paused')
        response = self.client.post(self.base + '/status',
            {'status': before['session_status'], 'confirm_location': True}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], 'paused')
        self.assertEqual(response.data['session_status'], 'paused')
        self.assertFalse(response.data['can_order'])
        self.stall.refresh_from_db()
        self.assertEqual(self.stall.current_session_id, session.id)

    def test_first_location_can_be_saved_before_opening_without_granting_approval(self):
        StallLocation.objects.filter(stall=self.stall).delete()
        response = self.client.post(self.base + '/status', {'status': 'closed',
            'address': '校园北门交接处', 'latitude': 31.235, 'longitude': 121.478}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        location = StallLocation.objects.get(stall=self.stall)
        self.assertEqual((location.address, location.latitude, location.longitude), ('校园北门交接处', 31.235, 121.478))
        self.assertEqual(response.data['address'], location.address)
        self.assertEqual(response.data['status'], 'closed')
        self.assertFalse(response.data['transaction_enabled'])
        self.assertFalse(response.data['can_order'])
        self.assertTrue(AuditLog.objects.filter(action='location_changed_requires_approval', target=str(self.stall.pk)).exists())

    def test_first_open_creates_location_only_after_confirmation_and_keeps_gate(self):
        StallLocation.objects.filter(stall=self.stall).delete()
        self.stall.current_session.status = 'closed'
        self.stall.current_session.save()
        body = {'status': 'open', 'address': '校园北门交接处', 'latitude': 31.235, 'longitude': 121.478}
        unconfirmed = self.client.post(self.base + '/status', body, format='json')
        self.assertEqual(unconfirmed.status_code, 400, unconfirmed.data)
        self.assertEqual(unconfirmed.data['code'], 'confirm_location_required')
        self.assertFalse(StallLocation.objects.filter(stall=self.stall).exists())
        self.assertEqual(self.stall.sessions.count(), 1)
        response = self.client.post(self.base + '/status', {**body, 'confirm_location': True}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], 'open')
        self.assertEqual(StallLocation.objects.filter(stall=self.stall).count(), 1)
        self.assertFalse(response.data['can_order'])
        self.assertFalse(response.data['transaction_enabled'])
        self.client.force_authenticate(self.student)
        rejected = self.client.post('/api/v1/orders', payload(self.stall, self.product), format='json')
        self.assertEqual(rejected.status_code, 409, rejected.data)
        self.assertEqual(rejected.data['code'], 'stall_unavailable')
        self.assertFalse(Order.objects.exists())

    def test_first_location_requires_complete_data_without_partial_writes(self):
        StallLocation.objects.filter(stall=self.stall).delete()
        for fields in ({}, {'address': '校园北门'}, {'latitude': 31.235, 'longitude': 121.478}):
            with self.subTest(fields=fields):
                response = self.client.post(self.base + '/status',
                    {'status': 'open', 'confirm_location': True, **fields}, format='json')
                self.assertEqual(response.status_code, 400, response.data)
                self.assertEqual(response.data['code'], 'location_required')
                self.assertIn('首次设置位置', response.data['detail'])
                self.assertFalse(StallLocation.objects.filter(stall=self.stall).exists())
                self.assertEqual(self.stall.sessions.count(), 1)
                self.stall.refresh_from_db()
                self.assertTrue(self.stall.transaction_enabled)

    def test_first_location_cannot_be_created_by_another_user(self):
        StallLocation.objects.filter(stall=self.stall).delete()
        self.client.force_authenticate(self.other)
        response = self.client.post(self.base + '/status', {'status': 'open', 'confirm_location': True,
            'address': '越权位置', 'latitude': 31.235, 'longitude': 121.478}, format='json')
        self.assertEqual(response.status_code, 404, response.data)
        self.assertFalse(StallLocation.objects.filter(stall=self.stall).exists())

    def test_reopening_expired_session_starts_fresh_and_preserves_order_snapshot(self):
        order = self.order()
        session = self.stall.current_session
        session.closes_at = timezone.now() - timedelta(minutes=1)
        session.save(update_fields=['closes_at'])
        before = self.client.get('/api/v1/merchant/stalls').data[0]
        self.assertEqual(before['status'], 'closed')
        self.assertEqual(before['session_status'], 'open')
        body = {'status': 'open', 'confirm_location': True}
        invalid = self.client.post(self.base + '/status',
            {**body, 'closes_at': (timezone.now() - timedelta(minutes=1)).isoformat()}, format='json')
        self.assertEqual(invalid.status_code, 400, invalid.data)
        self.assertEqual(invalid.data['code'], 'invalid_closing_time')
        self.assertEqual(self.stall.sessions.count(), 1)
        response = self.client.post(self.base + '/status', body, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['status'], 'open')
        self.assertEqual(response.data['session_status'], 'open')
        self.assertIsNone(response.data['closes_at'])
        self.assertTrue(response.data['can_order'])
        self.stall.refresh_from_db()
        self.assertNotEqual(self.stall.current_session_id, session.id)
        self.assertEqual(self.stall.sessions.count(), 2)
        order.refresh_from_db()
        self.assertEqual(order.pickup_address, '校园南门')
        self.assertEqual(order.stall_name, '测试烤冷面')
        self.assertEqual(order.items.get().unit_price_cents, 800)
        self.assertEqual(order.total_cents, 800)
        self.assertEqual(order.status, 'pending')

    def test_merchant_reply_persists_and_is_public_without_customer_identifier(self):
        order = self.order()
        Order.objects.filter(pk=order.pk).update(status='completed', completed_at=timezone.now())
        review = Review.objects.create(stall=self.stall, order=order, user=self.student, rating=4, content='少放辣椒更好')
        endpoint = f'/api/v1/merchant/reviews/{review.pk}/reply'
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.post(endpoint, {'content': '越权'}, format='json').status_code, 404)
        self.assertEqual(self.client.get('/api/v1/merchant/reviews', {'stall': self.stall.pk}).status_code, 404)
        self.client.force_authenticate(self.vendor)
        self.assertEqual(self.client.post(endpoint, {}, format='json').status_code, 400)
        result = self.client.post(endpoint, {'content': '谢谢建议，下次可以备注少辣。'}, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        self.assertIsNotNone(result.data['replied_at'])
        listed = self.client.get('/api/v1/merchant/reviews', {'stall': self.stall.pk}).data
        self.assertEqual(listed[0]['display_name'], '小同学')
        self.assertNotIn('user', listed[0])
        public = self.client.get(f'/api/v1/stalls/{self.stall.pk}').data['reviews'][0]
        self.assertEqual(public['merchant_reply'], '谢谢建议，下次可以备注少辣。')
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.post(endpoint, {'content': ''}, format='json').status_code, 404)
        review.refresh_from_db()
        self.assertEqual(review.merchant_reply, '谢谢建议，下次可以备注少辣。')
        self.client.force_authenticate(self.vendor)
        withdrawn = self.client.post(endpoint, {'content': ''}, format='json')
        self.assertEqual(withdrawn.status_code, 200, withdrawn.data)
        self.assertEqual(withdrawn.data['merchant_reply'], '')
        self.assertIsNone(withdrawn.data['replied_at'])
        public = self.client.get(f'/api/v1/stalls/{self.stall.pk}').data['reviews'][0]
        self.assertEqual(public['merchant_reply'], '')
        self.assertIsNone(public['replied_at'])
        self.assertEqual(public['content'], '少放辣椒更好')
        self.assertEqual(public['rating'], 4)

    def test_metrics_by_local_payment_day_instead_of_order_creation_day(self):
        first, second = self.order(), self.order()
        now = timezone.make_aware(datetime(2026, 9, 26, 0, 30))
        old_creation = now - timedelta(days=3)
        Order.objects.filter(pk=first.pk).update(created_at=old_creation, status='completed', payment_status='paid',
            paid_at=now - timedelta(minutes=10), completed_at=now - timedelta(minutes=5))
        Order.objects.filter(pk=second.pk).update(created_at=now - timedelta(minutes=5), status='completed', payment_status='paid',
            paid_at=now - timedelta(minutes=40), completed_at=now - timedelta(minutes=35))
        Follow.objects.create(user=self.student, stall=self.stall)
        Event.objects.create(type='stall_view', stall=self.stall)
        with patch('market.merchant.timezone.now', return_value=now):
            result = self.client.get('/api/v1/merchant/metrics', {'stall': self.stall.pk, 'days': 1})
        self.assertEqual(result.status_code, 200, result.data)
        data = result.data
        self.assertEqual(data['revenue_cents'], 800)
        self.assertEqual(data['paid_orders'], 1)
        self.assertEqual(data['today']['orders_completed'], 1)
        self.assertEqual(data['today']['orders_created'], 1)
        self.assertEqual(data['average_order_cents'], 800)
        self.assertEqual(data['followers'], 1)
        self.assertEqual(data['series'], [{'date': '2026-09-26', 'orders_created': 1, 'orders_completed': 1,
            'revenue_cents': 800, 'offline_revenue_cents': 800, 'online_revenue_cents': 0,
            'paid_orders': 1, 'refund_cents': 0, 'net_received_cents': 800}])
        self.assertEqual(str(data['recent_payments'][0]['id']), str(first.id))
        self.assertEqual(data['top_products'][0]['quantity'], 1)
        with patch('market.merchant.timezone.now', return_value=now):
            week = self.client.get('/api/v1/merchant/metrics', {'stall': self.stall.pk, 'days': 7}).data
        self.assertEqual(week['revenue_cents'], 1600)
        self.assertEqual(len(week['series']), 7)
        self.assertEqual(week['series'][-2]['revenue_cents'], 800)

    def test_metrics_selection_and_empty_values_are_real(self):
        data = self.client.get('/api/v1/merchant/metrics', {'stall': self.stall.pk, 'days': 30}).data
        self.assertEqual(data['revenue_cents'], 0)
        self.assertEqual(data['recent_payments'], [])
        self.assertIsNone(data['order_completion_rate'])
        self.assertEqual(len(data['series']), 30)
        self.assertEqual(self.client.get('/api/v1/merchant/metrics', {'days': 365}).status_code, 400)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get('/api/v1/merchant/metrics', {'stall': self.stall.pk}).status_code, 403)

    def test_image_upload_reencodes_and_strips_exif_in_own_stall_only(self):
        buffer = io.BytesIO()
        original = Image.new('RGB', (24, 18), '#ff781f')
        exif = Image.Exif()
        exif[315] = 'private metadata'
        original.save(buffer, 'JPEG', exif=exif)
        with tempfile.TemporaryDirectory(prefix='yanhuo-image-test-') as directory, override_settings(MEDIA_ROOT=directory):
            image = SimpleUploadedFile('customer-name.jpg', buffer.getvalue(), content_type='image/jpeg')
            result = self.client.post(self.base + '/image', {'file': image}, format='multipart')
            self.assertEqual(result.status_code, 201, result.data)
            self.assertTrue(result.data['url'].startswith(f'/media/merchants/{self.stall.pk}/'))
            self.assertNotIn('customer-name', result.data['url'])
            saved = Path(directory) / result.data['url'].removeprefix('/media/')
            with Image.open(saved) as cleaned:
                self.assertEqual(cleaned.format, 'JPEG')
                self.assertEqual(len(cleaned.getexif()), 0)
            self.client.force_authenticate(self.other)
            image = SimpleUploadedFile('a.jpg', buffer.getvalue(), content_type='image/jpeg')
            self.assertEqual(self.client.post(self.base + '/image', {'file': image}, format='multipart').status_code, 404)

    def test_image_upload_rejects_malformed_oversized_and_pixel_bomb(self):
        endpoint = self.base + '/image'
        result = self.client.post(endpoint, {'file': SimpleUploadedFile('fake.png', b'<script>bad</script>', content_type='image/png')}, format='multipart')
        self.assertEqual(result.status_code, 400)
        result = self.client.post(endpoint, {'file': SimpleUploadedFile('large.jpg', b'x' * (5 * 1024 * 1024 + 1))}, format='multipart')
        self.assertEqual(result.status_code, 400)
        self.assertEqual(result.data['code'], 'image_too_large')
        with patch('market.merchant.Image.open') as mocked:
            mocked.return_value.__enter__.return_value.width = 5000
            mocked.return_value.__enter__.return_value.height = 5000
            result = self.client.post(endpoint, {'file': SimpleUploadedFile('large.jpg', b'header')}, format='multipart')
        self.assertEqual(result.status_code, 400)
        self.assertEqual(result.data['code'], 'image_too_large')
