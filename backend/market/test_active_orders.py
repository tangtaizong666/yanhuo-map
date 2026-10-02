from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Order
from .services import cancel_order, create_order, merchant_action
from .tests import fixtures, payload


class ActiveOrderSummaryTests(TestCase):
    def setUp(self):
        self.student, self.other, self.vendor, self.stall, self.product = fixtures()
        self.client = APIClient()
        self.client.force_authenticate(self.student)

    def create(self, user=None):
        return create_order(user or self.student, payload(self.stall, self.product))[0]

    def summary(self):
        response = self.client.get('/api/v1/orders/active-summary')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        return response.data

    def test_empty_summary_and_authentication(self):
        self.assertEqual(self.summary(), {'user_id': self.student.pk,
            'counts': {'pending_payment': 0, 'pending': 0, 'preparing': 0, 'ready': 0, 'delivering': 0, 'arrived': 0, 'total': 0}, 'order': None})
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/api/v1/orders/active-summary').status_code, 403)

    def test_ready_priority_counts_privacy_and_customer_isolation(self):
        self.create(self.other)
        pending = self.create()
        preparing = self.create()
        ready = self.create()
        merchant_action(preparing.pk, self.vendor, 'accept')
        merchant_action(ready.pk, self.vendor, 'accept')
        merchant_action(ready.pk, self.vendor, 'ready')
        data = self.summary()
        self.assertEqual(data['counts'], {'pending_payment': 0, 'pending': 1, 'preparing': 1, 'ready': 1, 'delivering': 0, 'arrived': 0, 'total': 3})
        self.assertEqual(data['order']['id'], str(ready.pk))
        self.assertEqual(set(data['order']), {'id', 'stall_name', 'status', 'created_at',
            'expires_at', 'cancel_requested', 'fulfillment_type'})
        cancel_order(ready.pk, self.student, '临时有事')
        self.assertTrue(self.summary()['order']['cancel_requested'])
        merchant_action(ready.pk, self.vendor, 'approve_cancel')
        self.assertEqual(self.summary()['order']['id'], str(pending.pk))

    def test_terminal_orders_are_excluded(self):
        cancelled = self.create()
        rejected = self.create()
        completed = self.create()
        cancel_order(cancelled.pk, self.student, '')
        merchant_action(rejected.pk, self.vendor, 'reject', reason='售罄')
        merchant_action(completed.pk, self.vendor, 'accept')
        merchant_action(completed.pk, self.vendor, 'ready')
        merchant_action(completed.pk, self.vendor, 'confirm_payment')
        merchant_action(completed.pk, self.vendor, 'complete', code=completed.pickup_code)
        self.assertEqual(self.summary()['counts']['total'], 0)
        self.assertIsNone(self.summary()['order'])

    def test_expired_pending_is_cancelled_and_releases_inventory_once(self):
        order = self.create()
        Order.objects.filter(pk=order.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
        for _ in range(2):
            self.assertIsNone(self.summary()['order'])
        order.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(order.status, 'cancelled')
        self.assertEqual(self.product.stock, 5)
