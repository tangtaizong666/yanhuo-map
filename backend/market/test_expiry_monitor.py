"""Read-only backlog diagnostics share the expiry worker's eligibility rules."""
from datetime import timedelta
from io import StringIO
import json
import uuid
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import Order, PaymentAttempt, PaymentRefund, Stall
from .runtime_health import WORKERS, operations_status, record_worker_success
from .services import create_order, expire_pending_orders
from .tests import fixtures, payload


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    DEMO_MODE=True, PRODUCTION=False, SERVICES_SIMULATION_ENABLED=False)
class ExpiryMonitorTests(TestCase):
    def setUp(self):
        self.student, self.other, self.vendor, self.stall, self.product = fixtures()
        self.order = create_order(self.student, payload(self.stall, self.product))[0]
        self.now = timezone.now()
        for worker in WORKERS:
            record_worker_success(worker)

    def overdue(self, seconds=121, **values):
        Order.objects.filter(pk=self.order.pk).update(
            expires_at=self.now-timedelta(seconds=seconds), **values)

    def status(self, **options):
        with patch('market.runtime_health.timezone.now', return_value=self.now):
            return operations_status(**options)

    def payment(self, status='paid'):
        return PaymentAttempt.objects.create(order=self.order, merchant=self.stall.merchant,
            account_key='fixture', mchid='fixture', appid='fixture',
            out_trade_no=uuid.uuid4().hex, amount_cents=self.order.total_cents,
            status=status, channel='native', expires_at=self.now)

    def test_fresh_worker_heartbeats_do_not_hide_overdue_orders_and_check_is_read_only(self):
        self.overdue()
        self.product.refresh_from_db()
        stock = self.product.stock
        record = self.status()
        self.assertEqual(record['status'], 'attention')
        self.assertTrue(all(worker['healthy'] for worker in record['workers']))
        self.assertEqual(record['order_expiry'], {
            'eligible_pending': 1, 'overdue_count': 1,
            'oldest_overdue_seconds': 121.0, 'overdue': True,
        })
        self.order.refresh_from_db(); self.product.refresh_from_db()
        self.assertEqual(self.order.status, 'pending')
        self.assertFalse(self.order.inventory_released)
        self.assertEqual(self.product.stock, stock)
        self.assertEqual(expire_pending_orders(), 1)
        recovered = self.status()
        self.assertEqual(recovered['status'], 'ok')
        self.assertEqual(recovered['order_expiry'], {
            'eligible_pending': 0, 'overdue_count': 0,
            'oldest_overdue_seconds': None, 'overdue': False,
        })

    def test_expiry_grace_and_command_threshold_are_independent_of_payment_age(self):
        self.overdue(120)
        self.assertEqual(self.status()['status'], 'ok')
        self.overdue(121)
        self.assertEqual(self.status(max_expiry_age=180)['status'], 'ok')
        output = StringIO()
        with patch('market.runtime_health.timezone.now', return_value=self.now):
            with self.assertRaises(CommandError):
                call_command('check_operations', stdout=output)
        self.assertTrue(json.loads(output.getvalue())['order_expiry']['overdue'])
        with patch('market.runtime_health.timezone.now', return_value=self.now):
            call_command('check_operations', '--max-expiry-age', '180', stdout=StringIO())

    def test_future_and_terminal_orders_are_not_expiry_backlog(self):
        self.assertEqual(self.status()['order_expiry']['eligible_pending'], 0)
        for state in ('preparing', 'ready', 'completed', 'cancelled', 'rejected'):
            self.overdue(900, status=state)
            with self.subTest(status=state):
                self.assertEqual(self.status()['order_expiry']['eligible_pending'], 0)

    def test_financial_holds_do_not_look_like_safely_releasable_stock(self):
        self.overdue(900, payment_review_required=True)
        self.assertEqual(self.status()['order_expiry']['eligible_pending'], 0)
        self.overdue(900, payment_review_required=False)
        payment = self.payment('pending')
        self.assertEqual(self.status()['order_expiry']['eligible_pending'], 0)
        payment.status = 'paid'; payment.save()
        refund = PaymentRefund.objects.create(order=self.order, payment=payment,
            out_refund_no=uuid.uuid4().hex, amount_cents=payment.amount_cents,
            reason='fixture unresolved refund', status='closed')
        self.assertEqual(self.status()['order_expiry']['eligible_pending'], 0)
        refund.resolved_at = self.now; refund.save()
        self.assertEqual(self.status()['order_expiry']['eligible_pending'], 1)
        self.overdue(900, payment_status='refunding')
        self.assertEqual(self.status()['order_expiry']['eligible_pending'], 0)

    def test_paid_delivery_can_need_expiry_but_paid_pickup_cannot(self):
        self.overdue(payment_status='paid', fulfillment_type='pickup')
        self.assertEqual(self.status()['order_expiry']['eligible_pending'], 0)
        self.overdue(payment_status='paid', fulfillment_type='delivery')
        self.assertEqual(self.status()['order_expiry']['eligible_pending'], 1)

    def test_disabled_simulator_and_non_demo_stalls_are_excluded(self):
        self.overdue(mode='simulation')
        self.assertEqual(self.status()['order_expiry']['eligible_pending'], 0)
        Stall.objects.filter(pk=self.stall.pk).update(is_demo=True)
        self.assertEqual(self.status()['order_expiry']['eligible_pending'], 1)
        self.payment('closed')  # Even a terminal online attempt requires the simulator.
        self.assertEqual(self.status()['order_expiry']['eligible_pending'], 0)
        self.assertEqual(expire_pending_orders(), 0)
        with override_settings(SERVICES_SIMULATION_ENABLED=True):
            self.assertEqual(self.status()['order_expiry']['eligible_pending'], 1)
            Stall.objects.filter(pk=self.stall.pk).update(is_demo=False)
            self.assertEqual(self.status()['order_expiry']['eligible_pending'], 0)

    def test_unprocessable_demo_does_not_starve_later_expiry_batch(self):
        other = create_order(self.other, payload(self.stall, self.product))[0]
        self.overdue(900, mode='simulation')
        Stall.objects.filter(pk=self.stall.pk).update(is_demo=True)
        self.payment('closed')
        Order.objects.filter(pk=other.pk).update(expires_at=self.now-timedelta(seconds=1))
        self.assertEqual(expire_pending_orders(limit=1), 1)
        self.order.refresh_from_db(); other.refresh_from_db()
        self.assertEqual((self.order.status, other.status), ('pending', 'cancelled'))

