"""Exercise service admission at API boundaries; gateway calls are test doubles."""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Order, PaymentAttempt, PaymentRefund, Stall
from .tests import fixtures, payload
from .test_payments import PaymentSetup, payment_result
from .wechatpay import GatewayError


@override_settings(DEMO_MODE=False, WECHAT_PAY_ENABLED=True)
class AdmissionApiTests(TestCase):
    def setUp(self):
        self.student, self.other, self.vendor, self.stall, self.product = fixtures()
        self.merchant = self.stall.merchant
        self.merchant.license_valid_until = timezone.localdate()
        self.merchant.save()
        self.client = APIClient()
        self.client.force_authenticate(self.student)

    def submit(self, data=None):
        return self.client.post('/api/v1/orders', data or payload(self.stall, self.product), format='json')

    def revoke(self, kind='expired'):
        if kind == 'expired': self.merchant.license_valid_until = timezone.localdate()-timedelta(days=1)
        elif kind == 'verification': self.merchant.is_verified = False
        elif kind == 'mobile': self.merchant.qualification_tier = 'mobile_vendor'
        elif kind == 'authorization':
            self.stall.transaction_enabled = False
            self.stall.save()
        self.merchant.save()

    def test_valid_through_today_then_expired_actual_checkout_is_rejected(self):
        today = timezone.localdate()
        with patch('market.admission.timezone.localdate', return_value=today):
            response = self.submit()
        self.assertEqual(response.status_code, 201, response.data)
        self.client.force_authenticate(self.other)
        with patch('market.admission.timezone.localdate', return_value=today+timedelta(days=1)):
            response = self.submit()
        self.assertEqual(response.status_code, 409, response.data)
        self.assertIn('有效期', response.data['detail'])
        self.assertEqual(Order.objects.count(), 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 4)

    def test_all_revocations_block_new_orders_and_keep_public_information(self):
        for kind in ('expired', 'verification', 'mobile', 'authorization'):
            with self.subTest(kind=kind):
                self.revoke(kind)
                response = self.submit()
                self.assertEqual(response.status_code, 409, response.data)
                detail = self.client.get(f'/api/v1/stalls/{self.stall.pk}')
                self.assertEqual(detail.status_code, 200)
                caps = detail.data['capabilities']
                self.assertTrue(caps['public_listing']['available'])
                self.assertFalse(caps['pickup_orders']['eligible'])
                self.assertFalse(detail.data['can_order'])
                self.assertFalse(detail.data['transaction_enabled'])
                self.assertFalse(caps['online_payment']['available'])
                self.assertFalse(caps['delivery_orders']['available'])
                self.merchant.license_valid_until = timezone.localdate()
                self.merchant.is_verified = True
                self.merchant.qualification_tier = 'storefront'
                self.merchant.save()
                self.stall.transaction_enabled = True
                self.stall.save()
        self.assertFalse(Order.objects.exists())
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 5)

    def test_idempotent_retry_after_revocation_returns_existing_reservation(self):
        data = payload(self.stall, self.product)
        created = self.submit(data)
        self.assertEqual(created.status_code, 201)
        self.revoke()
        repeated = self.submit(data)
        self.assertEqual(repeated.status_code, 200, repeated.data)
        self.assertEqual(created.data['id'], repeated.data['id'])
        self.assertEqual(Order.objects.count(), 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 4)
        data['note'] = 'different request'
        self.assertEqual(self.submit(data).data['code'], 'idempotency_conflict')

    def test_expired_delivery_checkout_rejects_before_reserving_inventory(self):
        self.revoke()
        data = {**payload(self.stall, self.product), 'fulfillment_type': 'delivery',
            'delivery_point_id': 999, 'expected_delivery_fee_cents': 300,
            'recipient_name': '同学', 'contact_phone': '13800000000'}
        response = self.submit(data)
        self.assertEqual(response.status_code, 409, response.data)
        self.assertIn('有效期', response.data['detail'])
        self.assertFalse(Order.objects.exists())
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 5)

    def test_existing_order_can_be_read_and_cancelled_after_revocation(self):
        order = self.submit().data
        self.revoke('verification')
        self.assertEqual(self.client.get(f"/api/v1/orders/{order['id']}").status_code, 200)
        cancelled = self.client.post(f"/api/v1/orders/{order['id']}/cancel", {}, format='json')
        self.assertEqual(cancelled.status_code, 200, cancelled.data)
        self.assertEqual(cancelled.data['status'], 'cancelled')
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 5)

    def test_existing_order_fulfilment_is_not_blocked_by_new_order_qualification(self):
        order = self.submit().data
        self.revoke()
        self.client.force_authenticate(self.vendor)
        for action in ('accept', 'ready', 'confirm_payment', 'complete'):
            if action == 'complete':
                self.client.force_authenticate(self.student)
                order = self.client.get(f"/api/v1/orders/{order['id']}").data
                self.client.force_authenticate(self.vendor)
            result = self.client.post(f"/api/v1/merchant/orders/{order['id']}/action",
                {'action': action, 'pickup_code': order['pickup_code']}, format='json')
            self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['status'], 'completed')

    def test_enabling_merchant_switches_cannot_bypass_revocation(self):
        self.revoke()
        self.client.force_authenticate(self.vendor)
        self.assertEqual(self.client.patch(f'/api/v1/merchant/stalls/{self.stall.pk}/profile',
            {'accepting_orders': True}, format='json').status_code, 409)
        self.assertEqual(self.client.patch(f'/api/v1/merchant/stalls/{self.stall.pk}/delivery',
            {'enabled': True}, format='json').status_code, 409)
        self.assertEqual(self.client.patch(f'/api/v1/merchant/stalls/{self.stall.pk}/profile',
            {'accepting_orders': False}, format='json').status_code, 200)
        self.assertEqual(self.client.patch(f'/api/v1/merchant/stalls/{self.stall.pk}/delivery',
            {'enabled': False}, format='json').status_code, 200)

    def test_capability_qualification_survives_closing_or_pausing_new_orders(self):
        self.stall.accepting_orders = False
        self.stall.save()
        self.stall.current_session.status = 'closed'
        self.stall.current_session.save()
        self.client.force_authenticate(self.vendor)
        services = self.client.get(f'/api/v1/merchant/stalls/{self.stall.pk}/services')
        self.assertTrue(services.data['capabilities']['pickup_orders']['eligible'])
        self.assertFalse(services.data['capabilities']['pickup_orders']['available'])

    def test_orderable_discovery_uses_same_qualification_as_checkout(self):
        from .admission import pickup_eligible_q
        from .discovery_queries import filter_status, visible_stall_query
        from .models import SiteConfiguration
        self.assertTrue(Stall.objects.filter(pickup_eligible_q(), pk=self.stall.pk).exists())
        self.revoke()
        self.assertFalse(filter_status(visible_stall_query('summary'), 'orderable', SiteConfiguration.current()).exists())
        self.assertTrue(visible_stall_query('summary').exists())

    @override_settings(DEMO_MODE=True, SERVICES_SIMULATION_ENABLED=False, PRODUCTION=False)
    def test_explicit_demo_keeps_simulated_pickup_without_real_qualification(self):
        self.revoke('mobile')
        self.stall.is_demo = True
        self.stall.save()
        result = self.submit()
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(result.data['mode'], 'simulation')
        detail = self.client.get(f'/api/v1/stalls/{self.stall.pk}').data
        self.assertEqual(detail['capabilities']['mode'], 'simulation')
        self.assertFalse(detail['wechat_payment']['available'])

    @override_settings(DEMO_MODE=True, SERVICES_SIMULATION_ENABLED=True, PRODUCTION=False)
    def test_simulation_environment_does_not_bypass_real_stall_qualification(self):
        self.revoke('mobile')
        self.assertEqual(self.submit().status_code, 409)


@override_settings(DEMO_MODE=False)
class AdmissionPaymentApiTests(PaymentSetup, TestCase):
    def revoke(self):
        merchant = self.stall.merchant
        merchant.license_valid_until = timezone.localdate()-timedelta(days=1)
        merchant.save()

    def test_new_online_payment_blocked_even_when_gateway_configuration_is_ready(self):
        self.revoke()
        result = self.api.post(f'/api/v1/orders/{self.order.pk}/payments/wechat', {'channel': 'native'})
        self.assertEqual(result.status_code, 409, result.data)
        self.assertFalse(PaymentAttempt.objects.exists())
        self.gateway.create_payment.assert_not_called()

    def test_existing_unknown_payment_may_query_but_not_recreate_after_revocation(self):
        self.gateway.create_payment.side_effect = GatewayError('TIMEOUT', '请求结果未知')
        payment = self.start()
        self.revoke()
        self.gateway.reset_mock()
        self.gateway.query_payment.side_effect = GatewayError('ORDER_NOT_EXIST', '未收到付款请求')
        result = self.api.post(f'/api/v1/orders/{self.order.pk}/payments/wechat', {'channel': 'native'})
        self.assertEqual(result.status_code, 200, result.data)
        self.gateway.query_payment.assert_called_once()
        self.gateway.create_payment.assert_not_called()
        payment.refresh_from_db()
        self.assertEqual(payment.error_code, 'ORDER_NOT_EXIST')
        self.assertEqual(PaymentAttempt.objects.count(), 1)
        self.assertNotIn('pay', result.data['allowed_actions'])
        self.assertIn('sync_payment', result.data['allowed_actions'])
        self.assertIn('close_payment', result.data['allowed_actions'])

    def test_revocation_between_intent_and_provider_call_blocks_new_charge(self):
        def configure_and_revoke(payment):
            self.revoke()
            return self.gateway
        with patch('market.payments._compatible_client', side_effect=configure_and_revoke):
            result = self.api.post(f'/api/v1/orders/{self.order.pk}/payments/wechat', {'channel': 'native'})
        self.assertEqual(result.status_code, 200, result.data)
        self.gateway.create_payment.assert_not_called()
        payment = PaymentAttempt.objects.get(order=self.order)
        self.assertEqual(payment.status, 'closed')
        self.assertEqual(payment.error_code, 'TRADE_ADMISSION_REVOKED')

    def test_existing_payment_result_and_refund_remain_available(self):
        payment = self.start()
        self.revoke()
        self.gateway.query_payment.side_effect = lambda number: payment_result(payment)
        result = self.api.post(f'/api/v1/orders/{self.order.pk}/payments/sync', {})
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['payment_status'], 'paid')
        self.api.force_authenticate(self.vendor)
        result = self.api.post(f'/api/v1/merchant/orders/{self.order.pk}/refund', {'reason': '资格变更后协商退款'})
        self.assertEqual(result.status_code, 200, result.data)
        self.assertTrue(PaymentRefund.objects.filter(order=self.order).exists())
        self.gateway.refund.assert_called_once()
