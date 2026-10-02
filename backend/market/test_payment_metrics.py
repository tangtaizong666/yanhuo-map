"""Receipt/refund reports are cash movements on their own Shanghai calendar dates."""
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from .models import MerchantProfile, Order, PaymentAttempt, PaymentRefund, Stall
from .tests import fixtures


SHANGHAI = ZoneInfo('Asia/Shanghai')


class PaymentMetricsTests(TestCase):
    def setUp(self):
        self.student, self.other, self.vendor, self.stall, self.product = fixtures()
        self.client = APIClient()
        self.client.force_authenticate(self.vendor)
        self.now = datetime(2026, 9, 26, 12, 0, tzinfo=SHANGHAI)

    def order(self, cents=800, *, paid_at=None, method='wechat', payment_status='paid', stall=None,
              created_at=None):
        stall = stall or self.stall
        return Order.objects.create(
            user=self.student, stall=stall, stall_name=stall.name, status='ready',
            total_cents=cents, payment_status=payment_status, payment_method=method,
            paid_at=paid_at, created_at=created_at or self.now - timedelta(days=10),
            expires_at=self.now + timedelta(minutes=5), ready_at=self.now,
            pickup_address='校园南门', pickup_latitude=31.23, pickup_longitude=121.47,
            idempotency_key=uuid.uuid4().hex, request_hash='metrics-isolated-fixture')

    def refund(self, order, *, completed_at=None, status='success', created_at=None):
        payment = PaymentAttempt.objects.create(
            order=order, merchant=order.stall.merchant, account_key='isolated-test',
            mchid='1900000109', appid='wxmetrics', out_trade_no=uuid.uuid4().hex,
            amount_cents=order.total_cents, channel='native', status='paid', paid_at=order.paid_at,
            created_at=order.paid_at or self.now - timedelta(days=1),
            expires_at=self.now + timedelta(minutes=5))
        return PaymentRefund.objects.create(
            order=order, payment=payment, requested_by=self.vendor, out_refund_no=uuid.uuid4().hex,
            amount_cents=order.total_cents, reason='隔离测试退款', status=status,
            created_at=created_at or self.now - timedelta(days=1), completed_at=completed_at)

    def metrics(self, *, days=1, stall=None):
        with patch('market.merchant.timezone.now', return_value=self.now):
            response = self.client.get('/api/v1/merchant/metrics', {'stall': (stall or self.stall).pk, 'days': days})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_channel_receipts_keep_paid_refunding_and_refunded_gross_amounts(self):
        self.order(800, paid_at=self.now - timedelta(hours=1), method='offline')
        self.order(1200, paid_at=self.now - timedelta(hours=2), payment_status='refunding')
        returned = self.order(500, paid_at=self.now - timedelta(hours=3), payment_status='refunded')
        self.refund(returned, completed_at=self.now - timedelta(minutes=1))
        self.order(700, paid_at=self.now - timedelta(hours=4), payment_status='unpaid')
        self.order(600, paid_at=None)
        data = self.metrics()
        expected = {'revenue_cents': 2500, 'offline_revenue_cents': 800, 'online_revenue_cents': 1700,
                    'paid_orders': 3, 'refund_cents': 500, 'net_received_cents': 2000}
        for target in (data, data['today'], data['series'][0]):
            self.assertEqual({key: target[key] for key in expected}, expected)
        self.assertEqual(data['average_order_cents'], 833)
        rows = {str(row['id']): row for row in data['recent_payments']}
        returned_row = rows[str(returned.pk)]
        self.assertEqual(returned_row['payment_method'], 'wechat')
        self.assertEqual(returned_row['payment_status'], 'refunded')
        self.assertEqual(returned_row['refund_status'], 'success')
        self.assertTrue(returned_row['refunded'])
        self.assertEqual(returned_row['refunded_at'], self.now - timedelta(minutes=1))
        offline_row = next(row for row in rows.values() if row['payment_method'] == 'offline')
        self.assertFalse(offline_row['refunded'])
        self.assertIsNone(offline_row['refund_status'])

    def test_refund_uses_completed_day_and_net_can_be_negative(self):
        old_payment = self.order(5000, paid_at=self.now - timedelta(days=10), payment_status='refunded')
        self.refund(old_payment, completed_at=self.now - timedelta(minutes=1), created_at=self.now - timedelta(days=3))
        processing = self.order(800, paid_at=self.now - timedelta(days=2), payment_status='refunding')
        self.refund(processing, status='processing', completed_at=self.now - timedelta(minutes=2))
        future = self.order(900, paid_at=self.now - timedelta(days=2), payment_status='refunded')
        self.refund(future, completed_at=self.now + timedelta(minutes=1))
        missing_date = self.order(1000, paid_at=self.now - timedelta(days=2), payment_status='refunded')
        self.refund(missing_date, completed_at=None)
        data = self.metrics()
        for target in (data, data['today'], data['series'][0]):
            self.assertEqual(target['revenue_cents'], 0)
            self.assertEqual(target['paid_orders'], 0)
            self.assertEqual(target['refund_cents'], 5000)
            self.assertEqual(target['net_received_cents'], -5000)
        self.assertEqual(data['recent_payments'], [])

    def test_shanghai_midnight_and_cross_day_series_preserve_original_receipt(self):
        midnight = self.now.replace(hour=0, minute=0)
        before = self.order(800, paid_at=midnight - timedelta(seconds=1), payment_status='refunded')
        self.refund(before, completed_at=midnight)
        self.order(1200, paid_at=midnight, method='offline')
        self.order(600, paid_at=self.now + timedelta(seconds=1), method='offline')
        today = self.metrics()
        self.assertEqual(today['revenue_cents'], 1200)
        self.assertEqual(today['refund_cents'], 800)
        self.assertEqual(today['net_received_cents'], 400)
        week = self.metrics(days=7)
        self.assertEqual(week['revenue_cents'], 2000)
        self.assertEqual(week['refund_cents'], 800)
        self.assertEqual(week['net_received_cents'], 1200)
        yesterday, current = week['series'][-2:]
        self.assertEqual(yesterday['date'], '2026-09-25')
        self.assertEqual(yesterday['online_revenue_cents'], 800)
        self.assertEqual(yesterday['refund_cents'], 0)
        self.assertEqual(yesterday['net_received_cents'], 800)
        self.assertEqual(current['date'], '2026-09-26')
        self.assertEqual(current['offline_revenue_cents'], 1200)
        self.assertEqual(current['refund_cents'], 800)

    def test_foreign_stall_receipts_and_refunds_are_excluded(self):
        second_vendor = User.objects.create_user('metrics-second-vendor')
        second_profile = MerchantProfile.objects.create(user=second_vendor, business_name='其他经营主体')
        other_stall = Stall.objects.create(merchant=second_profile, area=self.stall.area, name='其他摊位', category='小吃')
        foreign = self.order(9900, stall=other_stall, paid_at=self.now - timedelta(minutes=10), payment_status='refunded')
        self.refund(foreign, completed_at=self.now - timedelta(minutes=1))
        self.order(800, paid_at=self.now - timedelta(minutes=5), method='offline')
        own = self.metrics()
        self.assertEqual(own['revenue_cents'], 800)
        self.assertEqual(own['refund_cents'], 0)
        with patch('market.merchant.timezone.now', return_value=self.now):
            all_owned = self.client.get('/api/v1/merchant/metrics', {'days': 1})
            forbidden = self.client.get('/api/v1/merchant/metrics', {'stall': other_stall.pk, 'days': 1})
        self.assertEqual(all_owned.data['revenue_cents'], 800)
        self.assertEqual(all_owned.data['refund_cents'], 0)
        self.assertEqual(forbidden.status_code, 404)
        self.client.force_authenticate(self.student)
        self.assertEqual(self.client.get('/api/v1/merchant/metrics').status_code, 403)

    def test_empty_reports_have_numeric_zero_for_all_cash_fields(self):
        data = self.metrics(days=7)
        expected = {'revenue_cents': 0, 'offline_revenue_cents': 0, 'online_revenue_cents': 0,
                    'paid_orders': 0, 'refund_cents': 0, 'net_received_cents': 0}
        for target in (data, data['today'], *data['series']):
            self.assertEqual({key: target[key] for key in expected}, expected)
