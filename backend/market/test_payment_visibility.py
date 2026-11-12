"""HTTP regressions with an actual configured merchant collection-image path."""
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Order, PaymentAttempt, PaymentRefund
from .services import merchant_action
from .tests import fixtures, payload


@override_settings(DEMO_MODE=False)
class PaymentVisibilityTests(TestCase):
    def setUp(self):
        self.student, self.other, self.vendor, self.stall, self.product = fixtures()
        self.stall.payment_qr_image = f'/media/merchants/{self.stall.pk}/qr-collection-code.png'
        self.stall.save(update_fields=['payment_qr_image'])
        self.client = APIClient()
        self.client.force_authenticate(self.student)
        response = self.client.post('/api/v1/orders', payload(self.stall, self.product), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.order = Order.objects.get(pk=response.data['id'])

    def details(self):
        response = self.client.get(f'/api/v1/orders/{self.order.pk}')
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def assert_entry(self, allowed):
        data = self.details()
        self.assertEqual(data['offline_payment_available'], allowed)
        self.assertEqual(data['stall_payment_qr_image'], self.stall.payment_qr_image if allowed else '')
        # Lists must obey the same contract, not leak an image hidden on detail.
        listed = self.client.get('/api/v1/orders')
        self.assertEqual(listed.status_code, 200)
        row = next(row for row in listed.data if row['id'] == str(self.order.pk))
        self.assertEqual(row['offline_payment_available'], allowed)
        self.assertEqual(row['stall_payment_qr_image'], data['stall_payment_qr_image'])

    def ready(self):
        merchant_action(self.order.pk, self.vendor, 'accept')
        merchant_action(self.order.pk, self.vendor, 'ready')
        self.order.refresh_from_db()

    def payment(self, status):
        return PaymentAttempt.objects.create(order=self.order, merchant=self.stall.merchant,
            account_key='test-only', mchid='test-merchant', appid='test-app',
            out_trade_no=uuid.uuid4().hex, channel='native', amount_cents=self.order.total_cents,
            status=status, expires_at=timezone.now() + timedelta(minutes=10))

    def test_code_follows_real_order_progress_and_cancel_decision(self):
        self.assert_entry(False)
        merchant_action(self.order.pk, self.vendor, 'accept')
        self.assert_entry(False)
        merchant_action(self.order.pk, self.vendor, 'ready')
        self.assert_entry(True)
        response = self.client.post(f'/api/v1/orders/{self.order.pk}/cancel', {'reason': '暂时不能取餐'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data['cancel_requested'])
        self.assertFalse(response.data['offline_payment_available'])
        self.assertEqual(response.data['stall_payment_qr_image'], '')
        self.assert_entry(False)
        merchant_action(self.order.pk, self.vendor, 'deny_cancel')
        self.assert_entry(True)
        merchant_action(self.order.pk, self.vendor, 'confirm_payment')
        self.assert_entry(False)

    def test_unavailable_states_never_expose_configured_code(self):
        self.ready()
        cases = [
            {'status': state} for state in ('pending_payment', 'pending', 'preparing', 'completed', 'cancelled', 'rejected')
        ] + [
            {'payment_status': state} for state in ('paid', 'refunding', 'refunded')
        ] + [
            {'cancel_requested': True}, {'payment_review_required': True},
            {'fulfillment_type': 'delivery'}, {'mode': 'simulation'}, {'payment_method': 'wechat'},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                Order.objects.filter(pk=self.order.pk).update(status='ready', payment_status='unpaid',
                    payment_method='offline', cancel_requested=False, payment_review_required=False,
                    fulfillment_type='pickup', mode='live')
                Order.objects.filter(pk=self.order.pk).update(**changes)
                self.assert_entry(False)

    def test_payment_outcomes_require_verified_close_before_offline_entry(self):
        self.ready()
        payment = self.payment('creating')
        for status in ('creating', 'pending', 'reconcile', 'review', 'paid'):
            with self.subTest(status=status):
                PaymentAttempt.objects.filter(pk=payment.pk).update(status=status)
                # Deliberately leave the order offline/unpaid to test an inconsistent
                # snapshot cannot bypass the financial records.
                self.assert_entry(False)
        PaymentAttempt.objects.filter(pk=payment.pk).update(status='closed')
        self.assert_entry(True)

    def test_older_active_payment_is_not_hidden_by_newer_closed_attempt(self):
        self.ready()
        self.payment('reconcile')
        self.payment('closed')
        self.assert_entry(False)

    def test_refund_history_never_offers_another_collection(self):
        self.ready()
        payment = self.payment('closed')
        refund = PaymentRefund.objects.create(order=self.order, payment=payment,
            out_refund_no=uuid.uuid4().hex, amount_cents=self.order.total_cents,
            reason='测试退款', status='processing')
        for status in ('creating', 'processing', 'reconcile', 'abnormal', 'closed', 'success'):
            with self.subTest(status=status):
                PaymentRefund.objects.filter(pk=refund.pk).update(status=status)
                self.assert_entry(False)
        PaymentRefund.objects.filter(pk=refund.pk).update(status='closed', resolved_at=timezone.now())
        self.assert_entry(False)

    def test_new_order_admission_closure_preserves_existing_settlement(self):
        self.ready()
        self.stall.merchant.license_valid_until = timezone.localdate() - timedelta(days=1)
        self.stall.merchant.save(update_fields=['license_valid_until'])
        self.stall.transaction_enabled = False
        self.stall.save(update_fields=['transaction_enabled'])
        self.assert_entry(True)
        merchant_action(self.order.pk, self.vendor, 'confirm_payment')
        self.assert_entry(False)

    def test_foreign_customer_cannot_read_collection_details(self):
        self.ready()
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(f'/api/v1/orders/{self.order.pk}').status_code, 404)

    def test_existing_wechat_entry_is_hidden_when_trade_qualification_expires(self):
        self.ready()
        payment = self.payment('pending')
        payment.code_url = 'weixin://wxpay/UI-TEST-ONLY'
        payment.save(update_fields=['code_url'])
        Order.objects.filter(pk=self.order.pk).update(payment_method='wechat')
        configured = {'available': True, 'reason': '', 'channels': ['native', 'h5'], 'account_key': 'test-only'}
        with patch('market.payments.readiness', return_value=configured):
            self.assertEqual(self.details()['payment']['code_url'], payment.code_url)
            self.stall.merchant.license_valid_until = timezone.localdate() - timedelta(days=1)
            self.stall.merchant.save(update_fields=['license_valid_until'])
            data = self.details()
            self.assertFalse(data['wechat_payment']['available'])
            self.assertEqual(data['payment']['code_url'], '')
            self.assertEqual(data['payment']['h5_url'], '')
            self.assertIn('sync_payment', data['allowed_actions'])
            self.assertIn('close_payment', data['allowed_actions'])
        self.assert_entry(False)

    def test_rehearsal_never_serializes_real_gateway_urls(self):
        self.ready()
        payment = self.payment('pending')
        payment.code_url = 'weixin://wxpay/UI-TEST-ONLY'
        payment.h5_url = 'https://wx.tenpay.com/UI-TEST-ONLY'
        payment.save(update_fields=['code_url', 'h5_url'])
        Order.objects.filter(pk=self.order.pk).update(mode='simulation')
        data = self.details()
        self.assertEqual(data['payment']['code_url'], '')
        self.assertEqual(data['payment']['h5_url'], '')
        self.assert_entry(False)

    def test_untrusted_remote_code_is_not_released(self):
        self.ready()
        self.stall.payment_qr_image = 'https://untrusted.example/collection.png'
        self.stall.save(update_fields=['payment_qr_image'])
        self.assertEqual(self.details()['stall_payment_qr_image'], '')

    def test_unprotected_public_media_path_is_never_released_as_collection_code(self):
        self.ready()
        for unprotected in (
            f'/media/merchants/{self.stall.pk}/public-dish.jpg',
            f'/media/merchants/{self.stall.pk + 999}/qr-foreign.jpg',
            '/images/demo.jpg',
        ):
            with self.subTest(unprotected=unprotected):
                self.stall.payment_qr_image = unprotected
                self.stall.save(update_fields=['payment_qr_image'])
                self.assertEqual(self.details()['stall_payment_qr_image'], '')


@override_settings(DEMO_MODE=False)
class PaymentQrChangeTests(TestCase):
    def setUp(self):
        self.student, self.other, self.vendor, self.stall, self.product = fixtures()
        self.client = APIClient()
        self.client.force_authenticate(self.vendor)
        self.endpoint = f'/api/v1/merchant/stalls/{self.stall.pk}/profile'

    def test_code_must_be_this_stalls_upload_and_change_is_audited(self):
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage
        from .models import AuditLog
        foreign = f'/media/merchants/{self.stall.pk + 1000}/qr-foreign.jpg'
        response = self.client.patch(self.endpoint, {'payment_qr_image': foreign}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(self.client.patch(self.endpoint, {'payment_qr_image': '/images/demo.jpg'}, format='json').status_code, 400)
        # Even a real uploaded public image in this stall's directory must not be accepted as a QR code.
        public_rel = default_storage.save(f'merchants/{self.stall.pk}/public-dish.jpg', ContentFile(b'public'))
        self.addCleanup(lambda: default_storage.delete(public_rel))
        public_own = default_storage.url(public_rel)
        rejected_public = self.client.patch(self.endpoint, {'payment_qr_image': public_own}, format='json')
        self.assertEqual((rejected_public.status_code, rejected_public.data['code']), (400, 'invalid_payment_qr'))
        # An unbacked qr-* path that does not exist in storage must also be rejected.
        unbacked = f'/media/merchants/{self.stall.pk}/qr-missing.jpg'
        rejected_unbacked = self.client.patch(self.endpoint, {'payment_qr_image': unbacked}, format='json')
        self.assertEqual((rejected_unbacked.status_code, rejected_unbacked.data['code']), (400, 'invalid_payment_qr'))
        qr_rel = default_storage.save(f'merchants/{self.stall.pk}/qr-valid.jpg', ContentFile(b'qr-image'))
        self.addCleanup(lambda: default_storage.delete(qr_rel))
        own = default_storage.url(qr_rel)
        response = self.client.patch(self.endpoint, {'payment_qr_image': own}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.stall.refresh_from_db()
        self.assertEqual(self.stall.payment_qr_image, own)
        changed_at = self.stall.payment_qr_updated_at
        self.assertIsNotNone(changed_at)
        self.assertTrue(AuditLog.objects.filter(action='payment_qr_changed', target=str(self.stall.pk)).exists())
        # Re-saving the same image (the store form sends every field) is not a change.
        self.client.patch(self.endpoint, {'payment_qr_image': own, 'usual_hours': '18:00-23:00'}, format='json')
        self.stall.refresh_from_db()
        self.assertEqual(self.stall.payment_qr_updated_at, changed_at)
        self.assertEqual(AuditLog.objects.filter(action='payment_qr_changed').count(), 1)
        # Clearing the code is allowed.
        self.assertEqual(self.client.patch(self.endpoint, {'payment_qr_image': ''}, format='json').status_code, 200)

    def test_legacy_code_survives_unrelated_profile_saves(self):
        self.stall.payment_qr_image = '/media/merchants/test/collection-code.png'
        self.stall.save(update_fields=['payment_qr_image'])
        response = self.client.patch(self.endpoint, {'payment_qr_image': self.stall.payment_qr_image,
            'usual_hours': '18:00-23:00'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
