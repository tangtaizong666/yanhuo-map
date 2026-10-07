"""License completeness gates new charges, never the resolution of older funds."""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from .models import MerchantProfile, PaymentAttempt, PaymentRefund, Stall
from .services import merchant_action
from .test_payments import PaymentSetup, payment_result
from .tests import payload
from .wechatpay import GatewayError


@override_settings(DEMO_MODE=False,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class LicensePaymentTests(PaymentSetup, TestCase):
    def incomplete(self, **changes):
        MerchantProfile.objects.filter(pk=self.stall.merchant_id).update(**changes)

    def restore(self):
        self.incomplete(license_number='TEST-ONLY-NOT-A-REAL-LICENSE',
            license_valid_until=timezone.localdate()+timedelta(days=365))

    def test_missing_or_whitespace_license_and_missing_date_block_new_payment_before_gateway(self):
        for changes in ({'license_number': ''}, {'license_number': ' \t\u00a0\u3000'}, {'license_valid_until': None}):
            with self.subTest(changes=changes):
                self.restore()
                self.incomplete(**changes)
                result = self.api.post(f'/api/v1/orders/{self.order.pk}/payments/wechat', {'channel': 'native'})
                self.assertEqual(result.status_code, 409, result.data)
                self.assertEqual(result.data['code'], 'payment_unavailable')
                self.assertFalse(PaymentAttempt.objects.filter(order=self.order).exists())
                self.gateway.create_payment.assert_not_called()
                self.gateway.query_payment.assert_not_called()
                self.gateway.close_payment.assert_not_called()
        self.order.refresh_from_db()
        self.assertEqual((self.order.payment_status, self.order.payment_method), ('unpaid', 'offline'))

    def test_complete_license_valid_through_today_can_start_payment(self):
        self.incomplete(license_valid_until=timezone.localdate())
        result = self.api.post(f'/api/v1/orders/{self.order.pk}/payments/wechat', {'channel': 'native'})
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['payment']['status'], 'pending')
        self.assertTrue(result.data['payment']['code_url'])
        self.gateway.create_payment.assert_called_once()

    def test_loss_of_number_or_expiry_between_intent_and_provider_call_stops_new_charge(self):
        for changes in ({'license_number': ''}, {'license_valid_until': None}):
            with self.subTest(changes=changes):
                self.restore()
                self.gateway.reset_mock()
                def revoke_before_gateway(payment):
                    self.incomplete(**changes)
                    return self.gateway
                with patch('market.payments._compatible_client', side_effect=revoke_before_gateway):
                    result = self.api.post(f'/api/v1/orders/{self.order.pk}/payments/wechat', {'channel': 'native'})
                self.assertEqual(result.status_code, 200, result.data)
                self.assertEqual(result.data['payment']['status'], 'closed')
                payment = PaymentAttempt.objects.filter(order=self.order).first()
                self.assertEqual(payment.error_code, 'TRADE_ADMISSION_REVOKED')
                self.assertEqual(result.data['payment_method'], 'offline')
                self.gateway.create_payment.assert_not_called()
        self.assertEqual(PaymentAttempt.objects.filter(order=self.order, status='closed').count(), 2)

    def test_unknown_existing_payment_can_query_but_cannot_recreate_with_incomplete_license(self):
        self.gateway.create_payment.side_effect = GatewayError('TIMEOUT', '请求结果未知')
        payment = self.start()
        self.incomplete(license_number='', license_valid_until=None)
        self.gateway.reset_mock()
        self.gateway.query_payment.side_effect = GatewayError('ORDER_NOT_EXIST', '尚未收到付款请求')
        result = self.api.post(f'/api/v1/orders/{self.order.pk}/payments/wechat', {'channel': 'native'})
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['payment']['id'], str(payment.pk))
        self.assertEqual(result.data['payment']['status'], 'reconcile')
        self.assertNotIn('pay', result.data['allowed_actions'])
        self.assertIn('sync_payment', result.data['allowed_actions'])
        self.assertIn('close_payment', result.data['allowed_actions'])
        self.assertEqual(result.data['payment']['code_url'], '')
        self.gateway.query_payment.assert_called_once()
        self.gateway.create_payment.assert_not_called()
        self.assertEqual(PaymentAttempt.objects.filter(order=self.order).count(), 1)

    def test_missing_license_keeps_old_payment_query_and_verified_close(self):
        payment = self.start()
        self.incomplete(license_number='', license_valid_until=None)
        self.gateway.reset_mock()
        self.gateway.query_payment.side_effect = lambda number: payment_result(payment, 'NOTPAY')
        result = self.api.post(f'/api/v1/orders/{self.order.pk}/payments/sync', {})
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['payment']['status'], 'pending')
        self.assertEqual(result.data['payment']['code_url'], '')
        self.assertFalse(result.data['offline_payment_available'])
        closed = self.api.post(f'/api/v1/orders/{self.order.pk}/payments/close', {})
        self.assertEqual(closed.status_code, 200, closed.data)
        self.assertEqual(closed.data['payment']['status'], 'closed')
        self.assertTrue(closed.data['offline_payment_available'])
        self.gateway.close_payment.assert_called_once()
        self.gateway.create_payment.assert_not_called()

    def test_missing_license_keeps_old_payment_confirmation_and_refund(self):
        payment = self.start()
        self.incomplete(license_number='', license_valid_until=None)
        self.gateway.reset_mock()
        self.gateway.query_payment.side_effect = lambda number: payment_result(payment)
        paid = self.api.post(f'/api/v1/orders/{self.order.pk}/payments/sync', {})
        self.assertEqual(paid.status_code, 200, paid.data)
        self.assertEqual(paid.data['payment_status'], 'paid')
        self.api.force_authenticate(self.vendor)
        refunded = self.api.post(f'/api/v1/merchant/orders/{self.order.pk}/refund', {'reason': '资质待补协商退款'})
        self.assertEqual(refunded.status_code, 200, refunded.data)
        self.assertEqual(refunded.data['payment_status'], 'refunding')
        self.assertTrue(PaymentRefund.objects.filter(order=self.order, status='processing').exists())
        self.gateway.refund.assert_called_once()
        self.gateway.create_payment.assert_not_called()

    @override_settings(DEMO_MODE=True, SERVICES_SIMULATION_ENABLED=True, PRODUCTION=False)
    def test_explicit_demo_rehearsal_needs_no_real_license_and_never_uses_live_gateway(self):
        self.incomplete(license_number='', license_valid_until=None)
        Stall.objects.filter(pk=self.stall.pk).update(is_demo=True, simulation_payment_enabled=True)
        result = self.api.post('/api/v1/orders', payload(self.stall, self.product), format='json')
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(result.data['mode'], 'simulation')
        order_id = result.data['id']
        for action in ('accept', 'ready'):
            merchant_action(order_id, self.vendor, action)
        simulated = self.api.post(f'/api/v1/orders/{order_id}/payments/wechat', {'channel': 'simulation'})
        self.assertEqual(simulated.status_code, 200, simulated.data)
        self.assertEqual(simulated.data['payment']['mode'], 'simulation')
        self.assertEqual(simulated.data['payment']['code_url'], '')
        self.assertEqual(simulated.data['stall_payment_qr_image'], '')
        self.gateway.create_payment.assert_not_called()
