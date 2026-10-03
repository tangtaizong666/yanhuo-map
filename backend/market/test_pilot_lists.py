import uuid
from datetime import timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Order, PaymentAttempt, PaymentRefund, Stall, StallLocation, Review, SiteConfiguration, BusinessSession
from .operational_models import WorkerHeartbeat
from .runtime_health import operations_status, record_worker_failure, record_worker_success
from .tests import fixtures


@override_settings(DEMO_MODE=True)
class PilotListTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.student, cls.other, cls.vendor, cls.stall, cls.product = fixtures()

    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(self.student)

    def order(self, **kwargs):
        return Order.objects.create(**{
            'user': self.student, 'stall': self.stall, 'stall_name': self.stall.name,
            'status': 'completed', 'payment_status': 'paid', 'total_cents': 1000,
            'expires_at': timezone.now()+timedelta(minutes=5), 'pickup_address': '测试位置',
            'pickup_latitude': 31, 'pickup_longitude': 121, 'idempotency_key': uuid.uuid4().hex,
            'request_hash': 'test', **kwargs})

    def test_cursor_is_bounded_and_stable_with_equal_timestamps(self):
        now = timezone.now()
        expected = {str(self.order(created_at=now).pk) for _ in range(65)}
        first = self.api.get('/api/v1/orders', {'pagination': 'cursor'})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(len(first.data['results']), 30)
        self.assertEqual(first.data['counts']['all'], 65)
        self.order(created_at=now+timedelta(seconds=1))
        ids = [row['id'] for row in first.data['results']]
        cursor = first.data['next']
        while cursor:
            response = self.api.get('/api/v1/orders', {'pagination': 'cursor', 'cursor': cursor})
            self.assertEqual(response.status_code, 200)
            ids += [row['id'] for row in response.data['results']]
            cursor = response.data['next']
        self.assertEqual(len(ids), 65)
        self.assertEqual(set(ids), expected)

    def test_attention_includes_old_terminal_financial_followup_without_history(self):
        for _ in range(35): self.order()
        active = self.order(status='preparing')
        old = self.order(status='cancelled', created_at=timezone.now()-timedelta(days=90))
        payment = PaymentAttempt.objects.create(order=old, merchant=self.stall.merchant,
            account_key='test', mchid='test', appid='test', out_trade_no=uuid.uuid4().hex,
            amount_cents=1000, status='paid', channel='native', expires_at=timezone.now())
        refund = PaymentRefund.objects.create(order=old, payment=payment, amount_cents=1000,
            out_refund_no=uuid.uuid4().hex, reason='test', status='closed')
        self.assertEqual(operations_status()['payments']['refunds_pending'], 1)
        response = self.api.get('/api/v1/orders', {'pagination': 'cursor', 'filter': 'attention'})
        self.assertEqual({row['id'] for row in response.data['results']}, {str(active.pk), str(old.pk)})
        self.assertEqual(response.data['counts']['followup'], 1)
        refund.resolved_at = timezone.now(); refund.save(update_fields=['resolved_at'])
        self.assertEqual(operations_status()['payments']['refunds_pending'], 0)
        response = self.api.get('/api/v1/orders', {'pagination': 'cursor', 'filter': 'followup'})
        self.assertEqual(response.data['results'], [])

    def test_cursor_cannot_cross_identity_filter_or_be_tampered(self):
        for _ in range(2): self.order()
        params = {'pagination': 'cursor', 'page_size': 1}
        cursor = self.api.get('/api/v1/orders', params).data['next']
        self.assertEqual(self.api.get('/api/v1/orders', {**params, 'cursor': cursor, 'filter': 'active'}).status_code, 400)
        self.api.force_authenticate(self.other)
        self.assertEqual(self.api.get('/api/v1/orders', {**params, 'cursor': cursor}).status_code, 400)
        self.assertEqual(self.api.get('/api/v1/orders', {**params, 'cursor': cursor+'x'}).status_code, 400)

    def test_reads_never_clean_global_expired_orders_and_legacy_is_preserved(self):
        row = self.order(status='pending', expires_at=timezone.now()-timedelta(minutes=1))
        with patch('market.views.expire_pending_orders', side_effect=AssertionError('read must not expire globally')):
            for url in ('/api/v1/orders', '/api/v1/orders/active-summary', f'/api/v1/orders/{row.pk}'):
                self.assertEqual(self.api.get(url).status_code, 200)
            self.api.force_authenticate(self.vendor)
            response = self.api.get('/api/v1/merchant/orders')
            self.assertEqual(response.status_code, 200)
            self.assertIsInstance(response.data, list)
        row.refresh_from_db()
        self.assertEqual(row.status, 'pending')

    def test_discovery_queries_do_not_grow_with_hidden_closed_stalls(self):
        SiteConfiguration.current()
        def read():
            with CaptureQueriesContext(connection) as queries:
                response = self.api.get('/api/v1/stalls', {'status': 'open'})
                self.assertEqual(response.status_code, 200)
                self.assertEqual([row['id'] for row in response.data['results']], [self.stall.pk])
            return len(queries)
        original = read()
        for index in range(9):
            stall = Stall.objects.create(merchant=self.stall.merchant, area=self.stall.area,
                name=f'已收摊 {index}', category='小吃')
            StallLocation.objects.create(stall=stall, address='校园', latitude=31, longitude=121)
        self.assertEqual(read(), original)

    def test_review_preview_is_bounded_but_rating_uses_all_reviews(self):
        for index in range(25):
            Review.objects.create(order=self.order(), user=self.student, stall=self.stall,
                rating=1 if index < 5 else 5, content=f'评价 {index}')
        response = self.api.get(f'/api/v1/stalls/{self.stall.pk}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['review_count'], 25)
        self.assertEqual(response.data['rating'], 4.2)
        self.assertEqual(len(response.data['reviews']), 20)
        self.assertEqual(response.data['reviews'][0]['content'], '评价 24')

    def test_sql_status_filter_matches_location_and_session_rules(self):
        now = timezone.now()
        expected = {'closed': [], 'stale': [], 'paused': [], 'open': [self.stall.pk]}
        variants = [({}, 'closed'), ({'status': 'closed'}, 'closed'),
            ({'status': 'open', 'closes_at': now-timedelta(seconds=1)}, 'closed'),
            ({'status': 'open', 'last_confirmed_at': now-timedelta(hours=2)}, 'stale'),
            ({'status': 'paused'}, 'paused')]
        for values, status in variants:
            stall = Stall.objects.create(merchant=self.stall.merchant, area=self.stall.area, name=status)
            if values:
                stall.current_session = BusinessSession.objects.create(stall=stall, **values)
                stall.save()
            expected[status].append(stall.pk)
        for status, ids in expected.items():
            response = self.api.get('/api/v1/stalls', {'status': status})
            self.assertEqual(response.status_code, 200)
            self.assertEqual({row['id'] for row in response.data['results']}, set(ids))


class WorkerHealthTests(TestCase):
    def test_health_probe_bypasses_guest_throttles(self):
        with patch('market.auth_limits.TrustedAnonRateThrottle.allow_request', side_effect=AssertionError('probe must not consume throttle')):
            self.assertEqual(APIClient().get('/api/v1/health').status_code, 200)

    def test_missing_stale_and_recovered_worker(self):
        self.assertEqual(operations_status(worker='expire_orders')['status'], 'unavailable')
        record_worker_success('expire_orders', 3)
        self.assertEqual(operations_status(worker='expire_orders')['status'], 'ok')
        WorkerHeartbeat.objects.filter(name='expire_orders').update(last_success_at=timezone.now()-timedelta(minutes=3))
        self.assertEqual(operations_status(worker='expire_orders')['status'], 'unavailable')
        record_worker_success('expire_orders', 2)
        self.assertEqual(operations_status(worker='expire_orders')['workers'][0]['processed_last_batch'], 2)

    def test_error_details_are_not_persisted_and_failure_count_survives_recovery(self):
        record_worker_failure('expire_orders', ValueError('do not store credentials or payloads'))
        record_worker_success('expire_orders', 1)
        row = WorkerHeartbeat.objects.get(name='expire_orders')
        self.assertEqual(row.failure_count, 1)
        self.assertEqual(row.last_error_code, '')
        self.assertEqual(operations_status(worker='expire_orders')['status'], 'ok')

    def test_check_command_fails_until_both_workers_have_progress(self):
        with self.assertRaises(CommandError): call_command('check_operations')
        from .runtime_health import WORKERS
        for name in WORKERS: record_worker_success(name)
        call_command('check_operations')
