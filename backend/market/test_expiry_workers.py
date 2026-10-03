"""Batch progression and actual PostgreSQL row-lock regressions."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event as ThreadEvent
from unittest import skipUnless

from django.db import close_old_connections, connection, connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from .models import BusinessSession, Event, Order, PaymentAttempt, PaymentRefund, Product, Stall, StallLocation
from .services import create_order, expire_pending_orders
from .tests import fixtures, payload


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ExpiryBatchTests(TestCase):
    def setUp(self):
        self.student, self.other, self.vendor, self.stall, self.product = fixtures()

    def test_closed_refunds_do_not_starve_later_expired_orders(self):
        held = create_order(self.student, payload(self.stall, self.product))[0]
        normal = create_order(self.student, payload(self.stall, self.product))[0]
        Order.objects.filter(pk=held.pk).update(fulfillment_type='delivery', payment_status='paid',
            expires_at=timezone.now()-timedelta(days=1))
        payment = PaymentAttempt.objects.create(order=held, merchant=self.stall.merchant,
            account_key='fixture', mchid='fixture', appid='fixture', out_trade_no=uuid.uuid4().hex,
            amount_cents=held.total_cents, status='paid', channel='native', expires_at=timezone.now())
        PaymentRefund.objects.create(order=held, payment=payment, out_refund_no=uuid.uuid4().hex,
            amount_cents=held.total_cents, reason='关闭后仍须补偿', status='closed')
        Order.objects.filter(pk=normal.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(expire_pending_orders(limit=1), 1)
        held.refresh_from_db(); normal.refresh_from_db()
        self.assertEqual((held.status, normal.status), ('pending', 'cancelled'))

    def test_batches_are_bounded_and_drain_without_duplicate_inventory(self):
        for _ in range(5): create_order(self.student, payload(self.stall, self.product))
        Order.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual([expire_pending_orders(limit=2) for _ in range(4)], [2, 2, 1, 0])
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 5)
        self.assertEqual(Event.objects.filter(type='order_expired').count(), 5)


@skipUnless(connection.vendor == 'postgresql', 'Requires real PostgreSQL row locks')
@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ExpiryWorkerConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.student, self.other, self.vendor, self.stall, self.product = fixtures()

    def while_locked(self, order, operation):
        locked, release = ThreadEvent(), ThreadEvent()
        def locker():
            close_old_connections()
            try:
                with transaction.atomic():
                    Order.objects.select_for_update().get(pk=order.pk)
                    locked.set()
                    if not release.wait(timeout=15): raise AssertionError('operation blocked on unrelated/claimed order')
            finally: connections.close_all()
        def invoke():
            close_old_connections()
            try: return operation()
            finally: connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            lock_future = pool.submit(locker)
            try:
                self.assertTrue(locked.wait(timeout=5))
                result = pool.submit(invoke).result(timeout=6)
            finally: release.set()
            lock_future.result(timeout=5)
        return result

    def test_first_locked_candidate_does_not_starve_the_next_batch(self):
        first = create_order(self.student, payload(self.stall, self.product))[0]
        second = create_order(self.other, payload(self.stall, self.product))[0]
        Order.objects.filter(pk=first.pk).update(expires_at=timezone.now()-timedelta(days=1))
        Order.objects.filter(pk=second.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.while_locked(first, lambda: expire_pending_orders(limit=1)), 1)
        first.refresh_from_db(); second.refresh_from_db()
        self.assertEqual((first.status, second.status), ('pending', 'cancelled'))
        self.assertEqual(expire_pending_orders(limit=1), 1)

    def test_other_stall_checkout_does_not_wait_for_expired_order_lock(self):
        old = create_order(self.student, payload(self.stall, self.product))[0]
        Order.objects.filter(pk=old.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
        stall = Stall.objects.create(merchant=self.stall.merchant, area=self.stall.area,
            name='第二摊位', category='小吃', transaction_enabled=True)
        StallLocation.objects.create(stall=stall, address='东门', latitude=31.23, longitude=121.47)
        stall.current_session = BusinessSession.objects.create(stall=stall, status='open', closes_at=timezone.now()+timedelta(hours=1))
        stall.save()
        product = Product.objects.create(stall=stall, name='第二摊位餐点', price_cents=500, stock=5)
        new, created = self.while_locked(old, lambda: create_order(self.other, payload(stall, product)))
        self.assertTrue(created)
        self.assertEqual(new.stall_id, stall.pk)
        old.refresh_from_db()
        self.assertEqual(old.status, 'pending')

    def test_two_workers_return_inventory_and_emit_expiry_only_once(self):
        Product.objects.filter(pk=self.product.pk).update(stock=12)
        for _ in range(10): create_order(self.student, payload(self.stall, self.product))
        Order.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
        barrier = Barrier(2)
        def run():
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                return expire_pending_orders(limit=10)
            finally: connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run), pool.submit(run)]
            counts = [future.result(timeout=15) for future in futures]
        self.assertEqual(sum(counts), 10)
        self.assertEqual(expire_pending_orders(limit=10), 0)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 12)
        self.assertEqual(Event.objects.filter(type='order_expired').count(), 10)
