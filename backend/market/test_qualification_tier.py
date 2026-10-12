"""Information-only vendors remain discoverable; real reservations need storefront admission."""
from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from .delivery import delivery_settings
from .errors import BusinessError
from .models import DeliveryPoint, MerchantProfile
from .serializers import OrderSerializer, StallSerializer
from .payment_config import get_merchant_payment_readiness
from .services import create_order
from .tests import fixtures, payload


@override_settings(DEMO_MODE=False, WECHAT_PAY_ENABLED=True)
class QualificationTierTests(TestCase):
    def setUp(self):
        self.student, self.other, self.vendor, self.stall, self.product = fixtures()
        self.merchant = self.stall.merchant
        self.merchant.qualification_tier = 'mobile_vendor'
        self.merchant.save()

    def test_new_merchants_default_to_mobile_vendor(self):
        profile = MerchantProfile.objects.create(user=self.other, business_name='新摊主')
        self.assertEqual(profile.qualification_tier, 'mobile_vendor')
        self.assertIn('找摊', profile.online_trade_reason())

    def test_mobile_vendor_never_opens_wechat_even_with_bound_account(self):
        with patch('market.payment_config._configuration') as configuration:
            result = get_merchant_payment_readiness(self.merchant)
        self.assertFalse(result['available'])
        self.assertIn('找摊', result['reason'])
        configuration.assert_not_called()

    def test_mobile_vendor_delivery_blocked_even_if_operator_approved(self):
        self.stall.delivery_approved = self.stall.delivery_enabled = True
        self.stall.save()
        point = DeliveryPoint.objects.create(area=self.stall.area, name='宿舍楼下', address='北门', latitude=31.23,
            longitude=121.47, is_active=True)
        self.stall.delivery_points.add(point)
        result = delivery_settings(self.stall)
        self.assertFalse(result['available'])
        self.assertIn('流动摊位', result['reason'])

    def test_mobile_vendor_cannot_bypass_admission_with_offline_payment(self):
        with self.assertRaises(BusinessError):
            create_order(self.student, payload(self.stall, self.product))

    def test_storefront_requires_both_addresses(self):
        self.merchant.qualification_tier = 'storefront'
        self.merchant.licensed_business_address = self.merchant.food_preparation_address = ''
        with self.assertRaises(ValidationError) as error:
            self.merchant.full_clean()
        self.assertEqual(set(error.exception.message_dict) & {'licensed_business_address', 'food_preparation_address'},
            {'licensed_business_address', 'food_preparation_address'})
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.merchant.save()

    def test_expired_license_suspends_online_trade(self):
        self.merchant.qualification_tier = 'storefront'
        self.merchant.licensed_business_address = '学府路 52 号'
        self.merchant.food_preparation_address = '学府路 52 号后厨'
        self.merchant.license_valid_until = timezone.localdate() - timedelta(days=1)
        self.merchant.save()
        self.assertIn('有效期', self.merchant.online_trade_reason())
        self.merchant.license_valid_until = timezone.localdate()
        self.assertEqual(self.merchant.online_trade_reason(), '')
        self.merchant.is_verified = False
        self.assertIn('核验', self.merchant.online_trade_reason())

    def test_vendor_qr_only_shown_on_unpaid_offline_orders(self):
        self.merchant.qualification_tier = 'storefront'
        self.merchant.save()
        self.stall.payment_qr_image = '/media/merchants/1/qr.jpg'
        self.stall.save()
        self.assertNotIn('payment_qr_image', StallSerializer(self.stall, context={}).data)
        order, _ = create_order(self.student, payload(self.stall, self.product))
        self.assertEqual(OrderSerializer(order).data['stall_payment_qr_image'], '')
        order.status = 'ready'
        self.assertEqual(OrderSerializer(order).data['stall_payment_qr_image'], '/media/merchants/1/qr.jpg')
        order.payment_status = 'paid'
        self.assertEqual(OrderSerializer(order).data['stall_payment_qr_image'], '')
        order.payment_status, order.status = 'unpaid', 'cancelled'
        self.assertEqual(OrderSerializer(order).data['stall_payment_qr_image'], '')
        order.stall.payment_qr_image = 'https://evil.example/qr.png'
        order.status = 'pending'
        self.assertEqual(OrderSerializer(order).data['stall_payment_qr_image'], '')


class LegacyOrderListLimitTests(TestCase):
    def test_unpaginated_list_is_capped(self):
        from rest_framework.test import APIClient
        from . import list_api
        student, _, _, stall, product = fixtures()
        for _ in range(3):
            order, _ = create_order(student, payload(stall, product))
            order.status = 'completed'
            order.save(update_fields=['status'])
        client = APIClient()
        client.force_authenticate(student)
        with patch.object(list_api, 'LEGACY_LIMIT', 2):
            response = client.get('/api/v1/orders')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)
