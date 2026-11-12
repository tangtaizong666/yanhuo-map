from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.permissions import AllowAny

from .admin import FeedbackVerificationForm
from .models import BusinessSession, Event, Feedback, Order, Stall
from .pilot_metrics import evidence
from .services import create_order
from .tests import fixtures, payload


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class PilotEvidenceTests(TestCase):
    def test_server_evidence_excludes_browser_events_and_unverified_reports(self):
        student, _, _, stall, product = fixtures()
        order, _ = create_order(student, payload(stall, product))
        now = timezone.now()
        start = now - timedelta(days=1)
        Order.objects.filter(pk=order.pk).update(created_at=now-timedelta(minutes=5),
            accepted_at=now-timedelta(minutes=3), status='completed', payment_status='paid',
            completed_at=now, paid_at=now)
        # Multiple sessions at the same merchant on the same day count once.
        BusinessSession.objects.create(stall=stall, status='closed')
        Event.objects.create(stall=stall, type='stall_view')
        Feedback.objects.create(stall=stall, user=student, kind='not_found', content='未核实', resolved=True)
        Feedback.objects.create(stall=stall, user=student, kind='wrong_location', content='已核实',
            verification='confirmed', verification_note='现场核对未在公示位置出摊')
        result = evidence(Order.objects.filter(mode='live'), Stall.objects.all(), start, now+timedelta(seconds=1), 'live')
        self.assertEqual(result['active_merchant_days'], 1)
        self.assertEqual(result['completed_orders'], 1)
        self.assertEqual(result['paying_customers'], 1)
        self.assertEqual(result['accept_p50_seconds'], 120)
        self.assertEqual(result['accept_sample_count'], 1)
        self.assertEqual(result['location_reports_received'], 2)
        self.assertEqual(result['location_reports_confirmed'], 1)
        simulated = evidence(Order.objects.filter(mode='simulation'), Stall.objects.all(), start, now, 'simulation')
        self.assertEqual(simulated['completed_orders'], 0)
        self.assertEqual(simulated['active_merchant_days'], 0)
        self.assertEqual(simulated['location_reports_confirmed'], 0)

    def test_operator_verification_requires_evidence_note(self):
        form = FeedbackVerificationForm(data={'verification': 'confirmed', 'verification_note': ''})
        self.assertFalse(form.is_valid())
        self.assertIn('verification_note', form.errors)


class RoutePermissionMatrixTests(TestCase):
    def test_only_explicit_public_routes_allow_anonymous_access(self):
        from .urls import urlpatterns
        public = {'health', 'config', 'auth/me', 'auth/csrf', 'auth/login', 'auth/register',
            'auth/logout', 'auth/recovery/reset', 'products', 'stalls/map', 'stalls',
            'stalls/<int:stall_id>', 'events', 'feedback', 'payments/wechat/notify/<slug:account_key>',
            'amap-proxy/_AMapService/<path:upstream_path>'}
        checked = set()
        for route in urlpatterns:
            name = str(route.pattern)
            with self.subTest(route=name):
                permissions = route.callback.cls.permission_classes
                self.assertEqual(AllowAny in permissions, name in public)
            checked.add(name)
        self.assertTrue(public <= checked)
