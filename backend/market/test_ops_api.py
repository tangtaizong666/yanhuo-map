"""Platform administrator (/api/v1/ops/*) governance API contracts."""
import uuid
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth.models import Permission, User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    Area,
    AuditLog,
    DeliveryPoint,
    Feedback,
    MerchantApplication,
    MerchantProfile,
    Order,
    PaymentAttempt,
    SiteConfiguration,
    Stall,
)
from .services import create_order
from .tests import fixtures, payload


@override_settings(
    DEMO_MODE=True,
    SERVICES_SIMULATION_ENABLED=False,
    PRODUCTION=False,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
)
class OpsGovernanceApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.student, cls.other, cls.vendor, cls.stall, cls.product = fixtures()
        cls.idle_user = User.objects.create_user('idle_student', password='password123', first_name='闲置同学')
        cls.applicant = User.objects.create_user('new_vendor', password='password123', first_name='李记摊主')
        cls.admin_user = User.objects.create_superuser('ops_root', 'ops@example.com', 'password123')
        cls.readonly_staff = User.objects.create_user('ops_viewer', password='password123', is_staff=True)
        cls.readonly_staff.user_permissions.add(
            Permission.objects.get(content_type__app_label='market', codename='view_merchantapplication'),
            Permission.objects.get(content_type__app_label='market', codename='view_feedback'),
        )

    def setUp(self):
        cache.clear()
        self.api = APIClient()

    def auth(self, user):
        self.api.force_authenticate(user)
        return self.api

    def test_role_isolation_and_staff_permission_boundaries(self):
        # Anonymous rejected
        anon_res = self.api.get('/api/v1/ops/overview')
        self.assertIn(anon_res.status_code, (401, 403))

        # Student and Merchant rejected; /auth/me does not include ops_permissions
        for non_admin in (self.student, self.vendor):
            self.auth(non_admin)
            me = self.api.get('/api/v1/auth/me')
            self.assertEqual(me.status_code, 200)
            me_user = me.json()
            self.assertNotIn('ops_permissions', me_user)
            self.assertNotIn('is_superuser', me_user)
            denied = self.api.get('/api/v1/ops/overview')
            self.assertEqual(denied.status_code, 403)
            self.assertEqual(denied.data['code'], 'forbidden')

        # Read-only staff sees exact permission flags and cannot mutate or read unauthorized modules
        self.auth(self.readonly_staff)
        me_staff = self.api.get('/api/v1/auth/me')
        self.assertEqual(me_staff.status_code, 200)
        perms = me_staff.json()['ops_permissions']
        self.assertTrue(perms['view_applications'])
        self.assertTrue(perms['view_feedback'])
        self.assertFalse(perms['review_applications'])
        self.assertFalse(perms['manage_config'])
        self.assertFalse(perms['view_orders'])

        self.assertEqual(self.api.get('/api/v1/ops/overview').status_code, 200)
        self.assertEqual(self.api.get('/api/v1/ops/applications').status_code, 200)
        self.assertEqual(self.api.get('/api/v1/ops/feedback').status_code, 200)

        self.assertEqual(self.api.patch('/api/v1/ops/overview', {'stale_minutes': 45}, format='json').status_code, 403)
        self.assertEqual(self.api.get('/api/v1/ops/orders').status_code, 403)
        self.assertEqual(
            self.api.post('/api/v1/ops/applications', {'username': self.applicant.username}, format='json').status_code,
            403,
        )
        self.assertEqual(
            self.api.patch(f'/api/v1/ops/users/{self.idle_user.pk}', {'is_active': False}, format='json').status_code,
            403,
        )

    def test_overview_metrics_and_site_config_audit(self):
        self.auth(self.admin_user)
        res = self.api.get('/api/v1/ops/overview?days=7&mode=live')
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res['Cache-Control'], 'private, no-store')
        self.assertIn('runtime', res.data)
        self.assertIn('pilot', res.data)
        self.assertIn('summary', res.data)

        patch_res = self.api.patch(
            '/api/v1/ops/overview',
            {'stale_minutes': 50, 'pending_minutes': 20},
            format='json',
        )
        self.assertEqual(patch_res.status_code, 200, patch_res.data)
        cfg = SiteConfiguration.current()
        self.assertEqual(cfg.stale_minutes, 50)
        self.assertEqual(cfg.pending_minutes, 20)
        self.assertTrue(AuditLog.objects.filter(action='ops_site_config_updated', actor=self.admin_user).exists())

    def test_assisted_onboarding_requires_applicant_confirmation_then_creates_safe_shell(self):
        self.auth(self.admin_user)
        created = self.api.post(
            '/api/v1/ops/applications',
            {
                'username': self.applicant.username,
                'business_name': '李记手抓饼经营部',
                'stall_name': '李记脆皮手抓饼',
                'contact_phone': '13800138111',
                'area_id': self.stall.area_id,
                'category': '小吃',
                'address_note': '二食堂东侧银杏树下',
                'description': '现烙手抓饼',
            },
            format='json',
        )
        self.assertEqual(created.status_code, 201, created.data)
        app_id = created.data['application']['id']
        self.assertEqual(created.data['application']['source'], 'assisted')
        self.assertEqual(created.data['application']['status'], 'draft')

        # Cannot approve before applicant personally confirms and submits
        premature = self.api.post(
            f'/api/v1/ops/applications/{app_id}/review',
            {'decision': 'approved', 'review_note': '直接通过'},
            format='json',
        )
        self.assertEqual(premature.status_code, 409)
        self.assertEqual(premature.data['code'], 'application_not_submitted')

        # Applicant logs in and submits personally
        self.auth(self.applicant)
        sub_res = self.api.post('/api/v1/merchant/application/submit', {}, format='json')
        self.assertEqual(sub_res.status_code, 200, sub_res.data)

        # Operator requests changes without note -> fails
        self.auth(self.admin_user)
        missing_note = self.api.post(
            f'/api/v1/ops/applications/{app_id}/review',
            {'decision': 'needs_changes', 'review_note': '   '},
            format='json',
        )
        self.assertEqual(missing_note.status_code, 400)
        self.assertEqual(missing_note.data['code'], 'review_note_required')

        # Operator approves -> creates unverified merchant + hidden non-trading stall
        approved = self.api.post(
            f'/api/v1/ops/applications/{app_id}/review',
            {'decision': 'approved', 'review_note': '主体资料核对无误，可先布置菜单'},
            format='json',
        )
        self.assertEqual(approved.status_code, 200, approved.data)
        self.assertEqual(approved.data['application']['status'], 'approved')

        merchant = MerchantProfile.objects.get(user=self.applicant)
        new_stall = Stall.objects.get(merchant=merchant)
        self.assertFalse(merchant.is_verified)
        self.assertFalse(new_stall.is_visible)
        self.assertFalse(new_stall.transaction_enabled)

        # Cannot enable online ordering while merchant is still unverified mobile_vendor
        blocked_grant = self.api.patch(
            f'/api/v1/ops/stalls/{new_stall.pk}',
            {'transaction_enabled': True},
            format='json',
        )
        self.assertEqual(blocked_grant.status_code, 400)
        self.assertEqual(blocked_grant.data['code'], 'qualification_incomplete')

        # Verify merchant as licensed storefront, then grant stall admission & verified location
        verified = self.api.patch(
            f'/api/v1/ops/merchants/{merchant.pk}',
            {
                'qualification_tier': 'storefront',
                'is_verified': True,
                'license_number': 'JY2310000998877',
                'licensed_business_address': '学府路 88 号 102 室',
                'food_preparation_address': '学府路 88 号后厨区',
                'license_valid_until': (date.today() + timedelta(days=180)).isoformat(),
            },
            format='json',
        )
        self.assertEqual(verified.status_code, 200, verified.data)
        self.assertTrue(verified.data['merchant']['is_verified'])

        granted = self.api.patch(
            f'/api/v1/ops/stalls/{new_stall.pk}',
            {
                'is_visible': True,
                'transaction_enabled': True,
                'delivery_approved': True,
                'location': {
                    'address': '二食堂东侧取餐口',
                    'latitude': 31.2315,
                    'longitude': 121.4725,
                },
            },
            format='json',
        )
        self.assertEqual(granted.status_code, 200, granted.data)
        self.assertTrue(granted.data['stall']['is_visible'])
        self.assertTrue(granted.data['stall']['transaction_enabled'])

        # Moving pickup coordinates without explicitly re-granting transaction_enabled revokes it safely
        moved = self.api.patch(
            f'/api/v1/ops/stalls/{new_stall.pk}',
            {
                'location': {
                    'address': '北门新取餐点',
                    'latitude': 31.2355,
                    'longitude': 121.4765,
                }
            },
            format='json',
        )
        self.assertEqual(moved.status_code, 200, moved.data)
        self.assertFalse(moved.data['stall']['transaction_enabled'])

    def test_areas_and_delivery_points_governance(self):
        self.auth(self.admin_user)
        area_res = self.api.post(
            '/api/v1/ops/areas',
            {
                'name': '图书馆东坪区',
                'subtitle': '晚间轻食停靠带',
                'latitude': 31.233,
                'longitude': 121.474,
            },
            format='json',
        )
        self.assertEqual(area_res.status_code, 201, area_res.data)
        area_id = area_res.data['area']['id']

        point_res = self.api.post(
            '/api/v1/ops/delivery-points',
            {
                'area_id': area_id,
                'name': '东坪北侧取餐架',
                'address': '图书馆东门台阶旁保温架',
                'latitude': 31.2332,
                'longitude': 121.4742,
                'is_active': True,
            },
            format='json',
        )
        self.assertEqual(point_res.status_code, 201, point_res.data)
        point_id = point_res.data['delivery_point']['id']

        toggle_res = self.api.patch(
            f'/api/v1/ops/delivery-points/{point_id}',
            {'is_active': False},
            format='json',
        )
        self.assertEqual(toggle_res.status_code, 200, toggle_res.data)
        self.assertFalse(toggle_res.data['delivery_point']['is_active'])

        with override_settings(DEMO_MODE=False):
            demo_blocked = self.api.post(
                '/api/v1/ops/areas',
                {'name': '禁止示例区', 'latitude': 31.2, 'longitude': 121.4, 'is_demo': True},
                format='json',
            )
            self.assertEqual(demo_blocked.status_code, 400)
            demo_area = Area.objects.create(name='旧示例区', latitude=31.21, longitude=121.41, is_demo=True)
            move_to_demo = self.api.patch(
                f'/api/v1/ops/delivery-points/{point_id}',
                {'area_id': demo_area.pk},
                format='json',
            )
            self.assertEqual(move_to_demo.status_code, 400)
            self.assertEqual(move_to_demo.data['code'], 'invalid_area')

        # Cross-area consistency: bound delivery point cannot change area while linked,
        # and moving stall area clears old-area delivery points automatically.
        other_area = Area.objects.create(name='西操场区', latitude=31.24, longitude=121.48)
        temp_stall = Stall.objects.create(
            merchant=self.stall.merchant,
            area_id=area_id,
            name='跨区校验摊位',
            category='小吃',
        )
        temp_stall.delivery_points.set([point_id])
        in_use = self.api.patch(
            f'/api/v1/ops/delivery-points/{point_id}',
            {'area_id': other_area.pk},
            format='json',
        )
        self.assertEqual(in_use.status_code, 409)
        self.assertEqual(in_use.data['code'], 'delivery_point_in_use')

        moved_stall = self.api.patch(
            f'/api/v1/ops/stalls/{temp_stall.pk}',
            {'area_id': other_area.pk},
            format='json',
        )
        self.assertEqual(moved_stall.status_code, 200, moved_stall.data)
        self.assertEqual(moved_stall.data['stall']['delivery_point_ids'], [])

    def test_orders_redaction_pagination_financial_resolution_and_csv_export(self):
        order_one, _ = create_order(self.student, payload(self.stall, self.product))
        order_two, _ = create_order(self.other, payload(self.stall, self.product))
        Order.objects.filter(pk=order_one.pk).update(status='ready')
        PaymentAttempt.objects.create(
            order=order_one,
            merchant=self.stall.merchant,
            mode='live',
            account_key='acct_ops_test',
            out_trade_no='otn_ops_001',
            channel='native',
            status='pending',
            amount_cents=order_one.total_cents,
            code_url='weixin://wxpay/bizpayurl?pr=SECRET',
            expires_at=timezone.now() + timedelta(minutes=15),
        )

        self.auth(self.admin_user)
        page1 = self.api.get('/api/v1/ops/orders?filter=all&page_size=1')
        self.assertEqual(page1.status_code, 200, page1.data)
        self.assertEqual(len(page1.data['results']), 1)
        self.assertTrue(page1.data['next'])
        first_row = page1.data['results'][0]
        self.assertEqual(first_row['pickup_code'], '')
        self.assertEqual(first_row['allowed_actions'], [])
        self.assertEqual(first_row['stall_payment_qr_image'], '')

        page2 = self.api.get(f"/api/v1/ops/orders?filter=all&page_size=1&cursor={page1.data['next']}")
        self.assertEqual(page2.status_code, 200, page2.data)
        self.assertEqual(len(page2.data['results']), 1)
        self.assertNotEqual(page1.data['results'][0]['id'], page2.data['results'][0]['id'])

        detail = self.api.get(f'/api/v1/ops/orders/{order_one.pk}')
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data['order']['pickup_code'], '')
        if detail.data['order']['payment']:
            self.assertEqual(detail.data['order']['payment']['code_url'], '')
        self.assertNotIn('code_url', detail.data['payments'][0])

        with patch('market.ops_api.verify_and_resolve') as mocked_verify:
            fin = self.api.post(
                f'/api/v1/ops/orders/{order_one.pk}/financial-resolution',
                {'operation': 'verify', 'reason': '运营后台发起渠道核对'},
                format='json',
            )
            self.assertEqual(fin.status_code, 200, fin.data)
            mocked_verify.assert_called_once_with(order_one.pk, self.admin_user, '运营后台发起渠道核对')

        today = timezone.localdate().isoformat()
        csv_res = self.api.get(
            f'/api/v1/ops/reconciliation/export?merchant_id={self.stall.merchant_id}&starts_on={today}&ends_on={today}',
            HTTP_ACCEPT='application/json, */*;q=0.8',
        )
        self.assertEqual(csv_res.status_code, 200)
        body = b''.join(csv_res.streaming_content).decode('utf-8')
        self.assertTrue(body.startswith('\ufeffrecord_type,merchant_id,'))
        self.assertIn('otn_ops_001', body)
        self.assertTrue(AuditLog.objects.filter(action='financial_reconciliation_exported', actor=self.admin_user).exists())

    def test_feedback_verification_requires_note_and_updates_pilot_evidence(self):
        report = Feedback.objects.create(
            stall=self.stall,
            user=self.student,
            kind='wrong_location',
            content='摊位实际在篮球场北口',
            contact='13900001111',
            location_snapshot={'address': '旧位置', 'latitude': 31.23, 'longitude': 121.47},
        )
        self.auth(self.admin_user)
        bad = self.api.patch(
            f'/api/v1/ops/feedback/{report.pk}',
            {'verification': 'confirmed', 'verification_note': ''},
            format='json',
        )
        self.assertEqual(bad.status_code, 400)
        self.assertIn('verification_note', bad.data['errors'])

        ok = self.api.patch(
            f'/api/v1/ops/feedback/{report.pk}',
            {
                'verification': 'confirmed',
                'verification_note': '校园巡查员 19:40 现场核实，已提醒摊主重新定位',
                'resolved': True,
            },
            format='json',
        )
        self.assertEqual(ok.status_code, 200, ok.data)
        self.assertEqual(ok.data['feedback']['verification'], 'confirmed')
        self.assertTrue(ok.data['feedback']['resolved'])

        overview = self.api.get('/api/v1/ops/overview?days=1&mode=live')
        self.assertEqual(overview.data['pilot']['location_reports_confirmed'], 1)

    def test_users_safety_guards_and_audit_log_feed(self):
        # Create active order for student so student & vendor both have active obligations
        create_order(self.student, payload(self.stall, self.product))
        self.auth(self.admin_user)

        # Self-deactivation blocked
        self_block = self.api.patch(
            f'/api/v1/ops/users/{self.admin_user.pk}',
            {'is_active': False},
            format='json',
        )
        self.assertEqual(self_block.status_code, 400)
        self.assertEqual(self_block.data['code'], 'self_deactivation_forbidden')

        # Other superuser deactivation blocked
        second_super = User.objects.create_superuser('backup_root', 'root2@example.com', 'password123')
        super_block = self.api.patch(
            f'/api/v1/ops/users/{second_super.pk}',
            {'is_active': False},
            format='json',
        )
        self.assertEqual(super_block.status_code, 400)
        self.assertEqual(super_block.data['code'], 'superuser_protected')

        # Student & vendor with open order blocked (409)
        for busy in (self.student, self.vendor):
            busy_res = self.api.patch(
                f'/api/v1/ops/users/{busy.pk}',
                {'is_active': False},
                format='json',
            )
            self.assertEqual(busy_res.status_code, 409)
            self.assertEqual(busy_res.data['code'], 'active_orders')

        # Idle user can be safely deactivated and reactivated
        deact = self.api.patch(
            f'/api/v1/ops/users/{self.idle_user.pk}',
            {'is_active': False},
            format='json',
        )
        self.assertEqual(deact.status_code, 200, deact.data)
        self.assertFalse(deact.data['user']['is_active'])

        logs = self.api.get('/api/v1/ops/audit-logs?action=ops_user_updated')
        self.assertEqual(logs.status_code, 200, logs.data)
        self.assertGreaterEqual(len(logs.data['results']), 1)

    def test_stalls_field_level_redaction_and_payment_routing_guard(self):
        m = self.stall.merchant
        m.contact_phone = '13911112222'
        m.qualification_note = '持证备案核查'
        m.license_number = 'JY999888777'
        m.licensed_business_address = '测试路 88 号'
        m.food_preparation_address = '测试路 88 号后厨'
        m.wechat_pay_account = 'wx_acct_initial'
        m.save()

        stall_only = User.objects.create_user('stall_only_ops', password='password123', is_staff=True)
        stall_only.user_permissions.add(
            Permission.objects.get(content_type__app_label='market', codename='view_stall'),
            Permission.objects.get(content_type__app_label='market', codename='change_stall'),
        )
        self.auth(stall_only)
        res = self.api.get('/api/v1/ops/stalls')
        self.assertEqual(res.status_code, 200, res.data)
        row_merchant = next(s['merchant'] for s in res.data['stalls'] if s['id'] == self.stall.pk)
        self.assertTrue(row_merchant['sensitive_redacted'])
        for redacted_key in (
            'contact_phone',
            'qualification_note',
            'license_number',
            'licensed_business_address',
            'food_preparation_address',
            'wechat_pay_account',
        ):
            self.assertEqual(row_merchant[redacted_key], '')

        patched_stall = self.api.patch(
            f'/api/v1/ops/stalls/{self.stall.pk}',
            {'public_phone_enabled': False},
            format='json',
        )
        self.assertEqual(patched_stall.status_code, 200, patched_stall.data)
        self.assertTrue(patched_stall.data['stall']['merchant']['sensitive_redacted'])
        self.assertEqual(patched_stall.data['stall']['merchant']['license_number'], '')

        merchant_editor = User.objects.create_user('merchant_editor_ops', password='password123', is_staff=True)
        merchant_editor.user_permissions.add(
            Permission.objects.get(content_type__app_label='market', codename='view_merchantprofile'),
            Permission.objects.get(content_type__app_label='market', codename='change_merchantprofile'),
        )
        self.auth(merchant_editor)
        full_res = self.api.get('/api/v1/ops/stalls')
        self.assertEqual(full_res.status_code, 200, full_res.data)
        full_merchant = next(s['merchant'] for s in full_res.data['stalls'] if s['id'] == self.stall.pk)
        self.assertFalse(full_merchant['sensitive_redacted'])
        self.assertEqual(full_merchant['license_number'], 'JY999888777')

        # Changing wechat_pay_account requires resolve_payments or superuser
        denied_pay = self.api.patch(
            f'/api/v1/ops/merchants/{m.pk}',
            {'wechat_pay_account': 'wx_acct_hijack'},
            format='json',
        )
        self.assertEqual(denied_pay.status_code, 403)
        self.assertEqual(denied_pay.data['code'], 'forbidden')

        merchant_editor.user_permissions.add(
            Permission.objects.get(content_type__app_label='market', codename='resolve_payments'),
        )
        merchant_editor = User.objects.get(pk=merchant_editor.pk)
        self.auth(merchant_editor)
        allowed_pay = self.api.patch(
            f'/api/v1/ops/merchants/{m.pk}',
            {'wechat_pay_account': 'wx_acct_verified'},
            format='json',
        )
        self.assertEqual(allowed_pay.status_code, 200, allowed_pay.data)
        self.assertEqual(allowed_pay.data['merchant']['wechat_pay_account'], 'wx_acct_verified')
