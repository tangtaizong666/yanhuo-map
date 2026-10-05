"""API contract used by merchant follow-up queues, without a second state machine.

History fixtures intentionally include terminal fulfilment with unresolved money
states. Reading a list must retain those facts, ownership and merchant redaction.
"""
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import Permission, User
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import MerchantProfile, Order, PaymentAttempt, PaymentRefund, Stall
from .tests import fixtures


@override_settings(DEMO_MODE=True, SERVICES_SIMULATION_ENABLED=False, PRODUCTION=False)
class MerchantFollowupContractTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.student, cls.other, cls.vendor, cls.stall, cls.product = fixtures()

    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(self.vendor)
        gateway = patch('market.payments.client_for', side_effect=AssertionError('List must not query or create a provider transaction'))
        self.gateway = gateway.start()
        self.addCleanup(gateway.stop)

    def order(self, *, stall=None, **changes):
        stall = stall or self.stall
        values = dict(user=self.student, stall=stall, stall_name=stall.name,
            status='cancelled', payment_method='wechat', payment_status='paid', total_cents=1000,
            created_at=timezone.now()-timedelta(days=90), expires_at=timezone.now()+timedelta(minutes=15),
            pickup_address='下单时的交接位置', pickup_latitude=31.23, pickup_longitude=121.47,
            pickup_code='18724635', idempotency_key=uuid.uuid4().hex, request_hash='followup-contract')
        return Order.objects.create(**{**values, **changes})

    def payment(self, order, **changes):
        values = dict(order=order, merchant=order.stall.merchant, mode=order.mode,
            account_key='private-account', mchid='1234567890', appid='private-appid',
            out_trade_no=uuid.uuid4().hex, transaction_id='TX'+uuid.uuid4().hex,
            channel='simulation' if order.mode == 'simulation' else 'native',
            amount_cents=order.total_cents, status='paid', expires_at=timezone.now()+timedelta(minutes=10))
        return PaymentAttempt.objects.create(**{**values, **changes})

    def refund(self, order, status):
        payment = self.payment(order)
        return PaymentRefund.objects.create(order=order, payment=payment, mode=order.mode,
            out_refund_no=uuid.uuid4().hex, amount_cents=order.total_cents,
            reason='订单售后跟进', status=status, error_message='需继续核对' if status in ('closed', 'abnormal', 'reconcile') else '')

    def rows(self, **params):
        response = self.api.get('/api/v1/merchant/orders', params)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsInstance(response.data, list)
        self.gateway.assert_not_called()
        return {row['id']: row for row in response.data}

    def test_terminal_orders_keep_all_unresolved_refund_states_in_list(self):
        expected = {}
        for status in ('cancelled', 'rejected', 'completed'):
            for refund_status in ('creating', 'processing', 'reconcile', 'abnormal', 'closed'):
                payment_status = 'paid' if refund_status == 'closed' else 'refunding'
                order = self.order(status=status, payment_status=payment_status)
                self.refund(order, refund_status)
                expected[str(order.pk)] = (status, payment_status, refund_status)
        rows = self.rows(stall=self.stall.pk)
        self.assertEqual(set(rows), set(expected))
        for key, state in expected.items():
            with self.subTest(order=key):
                row = rows[key]
                self.assertEqual((row['status'], row['payment_status'], row['refund']['status']), state)
                self.assertEqual(row['refund']['amount_cents'], row['total_cents'])
                self.assertEqual(row['refund']['reason'], '订单售后跟进')
                self.assertEqual(row['pickup_code'], '')

    def test_old_followup_is_not_truncated_by_new_completed_orders(self):
        old = self.order(created_at=timezone.now()-timedelta(days=365))
        self.refund(old, 'closed')
        for _ in range(30):
            self.order(status='completed', created_at=timezone.now())
        rows = self.rows(stall=self.stall.pk)
        self.assertEqual(len(rows), 31)
        self.assertEqual(list(rows)[-1], str(old.pk))
        self.assertEqual(rows[str(old.pk)]['refund']['status'], 'closed')

    def test_payment_review_and_uncertainty_are_exposed_without_resolving_them(self):
        flagged = self.order(status='completed', payment_review_required=True)
        self.payment(flagged)
        uncertain = self.order(status='ready', payment_status='unpaid')
        payment = self.payment(uncertain, status='reconcile', transaction_id=None, error_message='付款结果待确认')
        review = self.order(status='ready', payment_status='unpaid')
        self.payment(review, status='review', transaction_id=None)
        waiting = self.order(status='ready', payment_status='unpaid')
        waiting_payment = self.payment(waiting, status='pending', transaction_id=None)
        rows = self.rows()
        self.assertTrue(rows[str(flagged.pk)]['payment_review_required'])
        self.assertEqual(rows[str(uncertain.pk)]['payment']['status'], 'reconcile')
        self.assertEqual(rows[str(uncertain.pk)]['payment']['error_message'], '付款结果待确认')
        self.assertEqual(rows[str(review.pk)]['payment']['status'], 'review')
        self.assertEqual(rows[str(waiting.pk)]['payment']['status'], 'pending')
        self.assertFalse(rows[str(waiting.pk)]['payment_review_required'])
        payment.refresh_from_db(); flagged.refresh_from_db(); waiting_payment.refresh_from_db()
        self.assertEqual(payment.status, 'reconcile')
        self.assertTrue(flagged.payment_review_required)
        self.assertEqual(waiting_payment.status, 'pending')

    def test_list_is_scoped_to_merchant_or_authorized_staff_and_stall_selection(self):
        own = self.order()
        second_stall = Stall.objects.create(merchant=self.stall.merchant, area=self.stall.area, name='商家的第二摊位', category='小吃')
        second = self.order(stall=second_stall)
        foreign_merchant = MerchantProfile.objects.create(user=self.other, business_name='其他商家')
        foreign_stall = Stall.objects.create(merchant=foreign_merchant, area=self.stall.area, name='别人的摊位', category='小吃')
        foreign = self.order(stall=foreign_stall)
        self.assertEqual(set(self.rows()), {str(own.pk), str(second.pk)})
        self.assertEqual(set(self.rows(stall=self.stall.pk)), {str(own.pk)})
        self.assertEqual(self.rows(stall=foreign_stall.pk), {})
        self.api.force_authenticate(self.other)
        self.assertEqual(set(self.rows()), {str(foreign.pk)})
        self.api.force_authenticate(self.student)
        self.assertEqual(self.rows(), {})
        self.api.force_authenticate(None)
        self.assertEqual(self.api.get('/api/v1/merchant/orders').status_code, 403)
        staff = User.objects.create_user(username='followup_operator', is_staff=True)
        self.api.force_authenticate(staff)
        self.assertEqual(self.rows(), {})
        staff.user_permissions.add(Permission.objects.get(codename='view_order'))
        self.api.force_authenticate(User.objects.get(pk=staff.pk))
        self.assertEqual(len(self.rows()), 3)
        self.assertEqual(set(self.rows(stall=foreign_stall.pk)), {str(foreign.pk)})
        denied = self.api.post(f'/api/v1/merchant/orders/{foreign.pk}/action', {'action': 'complete'}, format='json')
        self.assertEqual(denied.status_code, 404)
        foreign.refresh_from_db()
        self.assertEqual(foreign.status, 'cancelled')

    @override_settings(DEMO_MODE=False)
    @patch('market.payments.readiness', return_value={
        'available': True, 'reason': '', 'channels': ['native', 'h5'], 'account_key': 'private-account'})
    def test_merchant_list_redacts_pickup_codes_payment_links_and_provider_identifiers(self, payment_readiness):
        # The shared fixture is a verified storefront. Explicitly configure live
        # payment readiness so empty merchant URLs prove redaction, not denial.
        orders = []
        for channel in ('native', 'h5'):
            order = self.order(status='ready', payment_status='unpaid')
            self.payment(order, status='pending', channel=channel, transaction_id=None,
                code_url='weixin://wxpay/private-entry', h5_url='https://wx.tenpay.com/private-entry')
            orders.append(order)
        rows = self.rows()
        for row in rows.values():
            self.assertTrue(row['wechat_payment']['available'])
            self.assertEqual(row['pickup_code'], '')
            self.assertEqual(row['payment']['code_url'], '')
            self.assertEqual(row['payment']['h5_url'], '')
            self.assertFalse({'account_key', 'mchid', 'appid', 'transaction_id', 'out_trade_no'} & set(row['payment']))
        self.api.force_authenticate(self.student)
        student = self.api.get(f'/api/v1/orders/{orders[0].pk}')
        self.assertEqual(student.status_code, 200)
        self.assertTrue(student.data['wechat_payment']['available'])
        self.assertEqual(student.data['pickup_code'], '')
        self.assertTrue(student.data['financial_hold_reason'])
        self.assertNotIn('complete', student.data['allowed_actions'])
        self.assertEqual(student.data['payment']['code_url'], 'weixin://wxpay/private-entry')
        payment_readiness.assert_called()
        self.gateway.assert_not_called()

    def test_historical_simulation_and_live_modes_survive_current_simulation_switch(self):
        expected = {}
        for mode in ('simulation', 'live'):
            order = self.order(mode=mode, payment_status='refunding')
            self.refund(order, 'processing')
            expected[str(order.pk)] = mode
        for enabled in (False, True):
            with self.subTest(simulation_enabled=enabled), override_settings(SERVICES_SIMULATION_ENABLED=enabled):
                rows = self.rows()
                self.assertEqual(set(rows), set(expected))
                for key, mode in expected.items():
                    row = rows[key]
                    self.assertEqual((row['mode'], row['payment']['mode'], row['refund']['mode']), (mode, mode, mode))
                    self.assertEqual(row['wechat_payment']['mode'], mode)
                    self.assertEqual(row['refund']['status'], 'processing')

    def test_delivery_issue_and_cancel_fields_preserve_terminal_history_and_current_state(self):
        active = self.order(status='delivering', fulfillment_type='delivery', delivery_issue='交接点暂时无法进入', cancel_requested=True)
        terminal = self.order(status='cancelled', fulfillment_type='delivery', payment_status='refunded', delivery_issue='原交接异常，已退款终止')
        self.refund(terminal, 'success')
        rows = self.rows()
        self.assertEqual(rows[str(active.pk)]['fulfillment_type'], 'delivery')
        self.assertEqual(rows[str(active.pk)]['delivery_issue'], '交接点暂时无法进入')
        self.assertTrue(rows[str(active.pk)]['cancel_requested'])
        self.assertEqual(rows[str(terminal.pk)]['status'], 'cancelled')
        self.assertEqual(rows[str(terminal.pk)]['refund']['status'], 'success')
        self.assertEqual(rows[str(terminal.pk)]['delivery_issue'], '原交接异常，已退款终止')
        self.assertFalse(rows[str(terminal.pk)]['cancel_requested'])
