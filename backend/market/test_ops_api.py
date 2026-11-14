"""Platform administrator (/api/v1/ops/*) governance API contracts."""
import uuid
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth.models import Permission, User
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .demo_keepalive import recover_startup_demo_stalls
from .models import (
    Area,
    AuditLog,
    BusinessSession,
    DeliveryPoint,
    Event,
    Feedback,
    MerchantApplication,
    MerchantProfile,
    Order,
    PaymentAttempt,
    Review,
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

    def test_ops_login_rejects_non_staff_without_clobbering_existing_session(self):
        csrf_client = APIClient(enforce_csrf_checks=True)
        csrf_res = csrf_client.get('/api/v1/auth/csrf')
        self.assertEqual(csrf_res.status_code, 200)
        token = csrf_res.data['csrfToken']

        # Log in as student on the normal endpoint first (simulating an open student tab)
        stu_login = csrf_client.post(
            '/api/v1/auth/login',
            {'username': self.student.username, 'password': 'DemoStrong123'},
            format='json',
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(stu_login.status_code, 200)
        token = csrf_client.get('/api/v1/auth/csrf').data['csrfToken']

        # Attempting merchant login on /ops/auth/login must fail with 403 BEFORE login(),
        # leaving the existing student session untouched!
        vendor_ops_attempt = csrf_client.post(
            '/api/v1/ops/auth/login',
            {'username': self.vendor.username, 'password': 'DemoStrong123'},
            format='json',
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(vendor_ops_attempt.status_code, 403)
        self.assertEqual(vendor_ops_attempt.data['code'], 'forbidden')
        self.assertEqual(vendor_ops_attempt['Cache-Control'], 'private, no-store')

        me_after = csrf_client.get('/api/v1/auth/me')
        self.assertEqual(me_after.json()['username'], self.student.username)

        # Valid admin credentials succeed and audit ops_login
        admin_login = csrf_client.post(
            '/api/v1/ops/auth/login',
            {'username': self.admin_user.username, 'password': 'password123'},
            format='json',
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(admin_login.status_code, 200)
        self.assertTrue(admin_login.data['is_staff'])
        self.assertTrue(
            AuditLog.objects.filter(
                actor=self.admin_user,
                action='ops_login',
                target=f'auth.User:{self.admin_user.pk}',
            ).exists()
        )

    def test_peer_staff_protection_simulation_point_guard_and_scoped_reconciliation_meta(self):
        user_mod = User.objects.create_user('ops_user_mod', password='password123', is_staff=True)
        user_mod.user_permissions.add(
            Permission.objects.get(content_type__app_label='auth', codename='view_user'),
            Permission.objects.get(content_type__app_label='auth', codename='change_user'),
        )
        self.auth(user_mod)

        # Peer staff cannot be deactivated or renamed by non-superuser user_mod
        peer_block = self.api.patch(
            f'/api/v1/ops/users/{self.readonly_staff.pk}',
            {'is_active': False},
            format='json',
        )
        self.assertEqual(peer_block.status_code, 403)
        self.assertEqual(peer_block.data['code'], 'forbidden')

        peer_rename = self.api.patch(
            f'/api/v1/ops/users/{self.readonly_staff.pk}',
            {'display_name': '被篡改管理员'},
            format='json',
        )
        self.assertEqual(peer_rename.status_code, 403)

        # Superuser can deactivate and reactivate peer staff
        self.auth(self.admin_user)
        su_deactivate = self.api.patch(
            f'/api/v1/ops/users/{self.readonly_staff.pk}',
            {'is_active': False},
            format='json',
        )
        self.assertEqual(su_deactivate.status_code, 200)
        self.assertFalse(su_deactivate.data['user']['is_active'])
        self.api.patch(
            f'/api/v1/ops/users/{self.readonly_staff.pk}',
            {'is_active': True},
            format='json',
        )

        # System simulation delivery points cannot be manually toggled or moved across areas
        area_b = Area.objects.create(name='北门片区', subtitle='', latitude=31.25, longitude=121.49, is_demo=True)
        sim_pt_a = DeliveryPoint.objects.create(
            area=self.stall.area,
            name='模拟校园交接点',
            address='仅供演练',
            latitude=31.23,
            longitude=121.47,
            is_active=False,
            is_simulation=True,
        )
        DeliveryPoint.objects.create(
            area=area_b,
            name='模拟校园交接点',
            address='仅供演练',
            latitude=31.25,
            longitude=121.49,
            is_active=False,
            is_simulation=True,
        )
        sim_patch = self.api.patch(
            f'/api/v1/ops/delivery-points/{sim_pt_a.pk}',
            {'area_id': area_b.pk, 'is_active': True},
            format='json',
        )
        self.assertEqual(sim_patch.status_code, 400)
        self.assertEqual(sim_patch.data['code'], 'simulation_point_managed')

        # Area with linked stalls/points cannot flip is_demo
        area_flip = self.api.patch(
            f'/api/v1/ops/areas/{self.stall.area_id}',
            {'is_demo': not self.stall.area.is_demo},
            format='json',
        )
        self.assertEqual(area_flip.status_code, 409)
        self.assertEqual(area_flip.data['code'], 'area_in_use')

        # Scoped reconciliation exporter gets reconciliation_merchants in overview without view_merchants
        exporter = User.objects.create_user('ops_exporter_only', password='password123', is_staff=True)
        exporter.user_permissions.add(
            Permission.objects.get(content_type__app_label='market', codename='export_financial_reconciliation'),
        )
        self.auth(exporter)
        ov = self.api.get('/api/v1/ops/overview')
        self.assertEqual(ov.status_code, 200)
        self.assertTrue(len(ov.data['reconciliation_merchants']) >= 1)

        missing_mid = self.api.get('/api/v1/ops/reconciliation/export?starts_on=2026-10-01&ends_on=2026-10-07')
        self.assertEqual(missing_mid.status_code, 400)
        self.assertEqual(missing_mid.data['code'], 'invalid_merchant')

    def _completed_order(self, buyer, *, created_offset_seconds=600, completed_offset_seconds=120):
        now = timezone.now()
        order, _ = create_order(buyer, payload(self.stall, self.product))
        Order.objects.filter(pk=order.pk).update(
            status='completed',
            payment_status='paid',
            created_at=now - timedelta(seconds=created_offset_seconds),
            paid_at=now - timedelta(seconds=completed_offset_seconds + 10),
            completed_at=now - timedelta(seconds=completed_offset_seconds),
        )
        order.refresh_from_db()
        return order

    def test_review_governance_anti_brushing_malicious_moderation_and_public_recalculation(self):
        # 1. Permission boundary: readonly_staff lacks view_review -> 403
        self.auth(self.readonly_staff)
        self.assertEqual(self.api.get('/api/v1/ops/reviews').status_code, 403)

        review_viewer = User.objects.create_user('ops_review_viewer', password='password123', is_staff=True)
        review_viewer.user_permissions.add(
            Permission.objects.get(content_type__app_label='market', codename='view_review')
        )

        # 2. Seed an honest 4-star review (with an inappropriate merchant reply) + 3 rapid 5-star brush reviews + 1 honest 1-star review
        honest_order = self._completed_order(self.other, created_offset_seconds=900, completed_offset_seconds=300)
        honest_review = Review.objects.create(
            order=honest_order,
            user=self.other,
            stall=self.stall,
            rating=4,
            content='面很筋道，酱稍微偏咸一点点。',
            merchant_reply='爱吃不吃，不吃滚出夜市！',
            replied_at=timezone.now(),
        )

        low_user = User.objects.create_user('honest_critic', password='password123', first_name='林同学')
        low_order = self._completed_order(low_user, created_offset_seconds=1200, completed_offset_seconds=200)
        low_review = Review.objects.create(
            order=low_order,
            user=low_user,
            stall=self.stall,
            rating=1,
            content='今天等了二十分钟，拿到的时候已经凉了。',
        )

        brush_reviews = []
        for idx in range(3):
            b_order = self._completed_order(self.student, created_offset_seconds=60, completed_offset_seconds=20)
            brush_reviews.append(
                Review.objects.create(
                    order=b_order,
                    user=self.student,
                    stall=self.stall,
                    rating=5,
                    content=f'好评返现加微领红包{idx}',
                )
            )

        # Viewer can read list & risk signals, but cannot mutate
        self.auth(review_viewer)
        list_res = self.api.get('/api/v1/ops/reviews?status=flagged')
        self.assertEqual(list_res.status_code, 200, list_res.data)
        self.assertEqual(list_res.data['counts']['all'], 5)
        self.assertEqual(list_res.data['counts']['flagged'], 5)
        flagged_by_id = {row['id']: row for row in list_res.data['results']}
        brush_item = flagged_by_id[brush_reviews[0].pk]
        flag_codes = {f['code'] for f in brush_item['risk_flags']}
        self.assertIn('repeat_pair', flag_codes)
        self.assertIn('rapid_completion', flag_codes)
        self.assertIn('suspicious_content', flag_codes)
        self.assertEqual(
            self.api.patch(
                f'/api/v1/ops/reviews/{brush_reviews[0].pk}',
                {'action': 'hide', 'moderation_reason': '越权尝试'},
                format='json',
            ).status_code,
            403,
        )

        # 3. Admin hides the 3 brushed reviews (reason required)
        self.auth(self.admin_user)
        missing_reason = self.api.patch(
            f'/api/v1/ops/reviews/{brush_reviews[0].pk}',
            {'action': 'hide', 'moderation_reason': '   '},
            format='json',
        )
        self.assertEqual(missing_reason.status_code, 400)

        for br in brush_reviews:
            hide_res = self.api.patch(
                f'/api/v1/ops/reviews/{br.pk}',
                {'action': 'hide', 'moderation_reason': '同客7天连刷3单且30秒极速完单并含返现引流词'},
                format='json',
            )
            self.assertEqual(hide_res.status_code, 200, hide_res.data)
            self.assertTrue(hide_res.data['review']['is_hidden'])
            self.assertEqual(hide_res.data['review']['moderation_status'], 'hidden')

        # 4. Honest 1-star complaint is cleared (mandatory reason required; kept public, removed from flagged queue!)
        missing_clear_reason = self.api.patch(
            f'/api/v1/ops/reviews/{low_review.pk}',
            {'action': 'clear', 'moderation_reason': '   '},
            format='json',
        )
        self.assertEqual(missing_clear_reason.status_code, 400)

        clear_res = self.api.patch(
            f'/api/v1/ops/reviews/{low_review.pk}',
            {'action': 'clear', 'moderation_reason': '核对履约耗时属实，属正常体验差评予以保留'},
            format='json',
        )
        self.assertEqual(clear_res.status_code, 200, clear_res.data)
        self.assertFalse(clear_res.data['review']['is_hidden'])
        self.assertEqual(clear_res.data['review']['moderation_status'], 'cleared')
        self.assertFalse(clear_res.data['review']['is_flagged'])

        # 5. Clear abusive merchant reply while keeping honest 4-star student review public
        clear_reply_res = self.api.patch(
            f'/api/v1/ops/reviews/{honest_review.pk}',
            {'action': 'clear_reply', 'moderation_reason': '商家回复含辱骂驱赶言论，予以清理'},
            format='json',
        )
        self.assertEqual(clear_reply_res.status_code, 200, clear_reply_res.data)
        self.assertEqual(clear_reply_res.data['review']['merchant_reply'], '')
        self.assertFalse(clear_reply_res.data['review']['is_flagged'])

        # Overview flagged count is now 0, hidden count is 3!
        ov = self.api.get('/api/v1/ops/overview').data['summary']
        self.assertEqual(ov['reviews_total'], 5)
        self.assertEqual(ov['reviews_hidden'], 3)
        self.assertEqual(ov['reviews_flagged'], 0)

        # 5b. Post-clearance merchant reply lifecycle:
        # Polite reply on cleared 1-star review stays unflagged; abusive new reply re-flags it!
        self.auth(self.vendor)
        polite_reply = self.api.post(
            f'/api/v1/merchant/reviews/{low_review.pk}/reply',
            {'content': '非常抱歉今天高峰期出餐慢了，我们明天增加人手改进！'},
            format='json',
        )
        self.assertEqual(polite_reply.status_code, 200, polite_reply.data)

        self.auth(self.admin_user)
        self.assertEqual(self.api.get('/api/v1/ops/overview').data['summary']['reviews_flagged'], 0)

        self.auth(self.vendor)
        abusive_reply = self.api.post(
            f'/api/v1/merchant/reviews/{low_review.pk}/reply',
            {'content': '嫌慢就滚出夜市别来买'},
            format='json',
        )
        self.assertEqual(abusive_reply.status_code, 200, abusive_reply.data)

        self.auth(self.admin_user)
        self.assertEqual(self.api.get('/api/v1/ops/overview').data['summary']['reviews_flagged'], 1)
        reflaged_list = self.api.get('/api/v1/ops/reviews?status=flagged').data
        self.assertEqual([r['id'] for r in reflaged_list['results']], [low_review.pk])

        clean_second_reply = self.api.patch(
            f'/api/v1/ops/reviews/{low_review.pk}',
            {'action': 'clear_reply', 'moderation_reason': '已复核评价后商家追加不当辱骂回复，清理回复保留原评'},
            format='json',
        )
        self.assertEqual(clean_second_reply.status_code, 200, clean_second_reply.data)
        self.assertFalse(clean_second_reply.data['review']['is_hidden'])
        self.assertEqual(clean_second_reply.data['review']['moderation_status'], 'cleared')
        self.assertFalse(clean_second_reply.data['review']['is_flagged'])
        self.assertEqual(self.api.get('/api/v1/ops/overview').data['summary']['reviews_flagged'], 0)
        reply_log = AuditLog.objects.filter(
            action='ops_review_reply_cleared', target=f'market.Review:{low_review.pk}'
        ).latest('id')
        self.assertEqual(len(reply_log.details.get('reply_sha256', '')), 64)
        self.assertGreater(reply_log.details.get('reply_length', 0), 0)

        # 5c. Ensure status=flagged does not drop older flagged items behind >300 normal unreviewed reviews
        ref_now = timezone.now()
        self.product.stock = 50
        self.product.save(update_fields=['stock'])
        old_flagged_order = self._completed_order(
            low_user, created_offset_seconds=86400 * 5 + 360, completed_offset_seconds=86400 * 5
        )
        old_flagged = Review.objects.create(
            order=old_flagged_order,
            user=low_user,
            stall=self.stall,
            rating=1,
            content='五天前等了很久才拿到',
        )
        Review.objects.filter(pk=old_flagged.pk).update(created_at=ref_now - timedelta(days=5))
        bulk_users = [User(username=f'bulk_stu_{i}') for i in range(305)]
        User.objects.bulk_create(bulk_users)
        bulk_users = list(User.objects.filter(username__startswith='bulk_stu_').order_by('id'))
        bulk_orders = [
            Order(
                number=f'BULKREV{idx:04d}',
                user=u,
                stall=self.stall,
                stall_name=self.stall.name,
                mode='live',
                status='completed',
                payment_method='offline',
                payment_status='paid',
                total_cents=700,
                pickup_code=f'{1000 + idx}',
                pickup_address='测试取餐点',
                pickup_latitude=30.5,
                pickup_longitude=114.3,
                expires_at=ref_now + timedelta(minutes=15),
                created_at=ref_now - timedelta(minutes=8),
                completed_at=ref_now,
                idempotency_key=f'bulk-idem-{idx}',
                request_hash=f'bulk-hash-{idx}',
            )
            for idx, u in enumerate(bulk_users)
        ]
        Order.objects.bulk_create(bulk_orders)
        bulk_orders = list(Order.objects.filter(user__username__startswith='bulk_stu_').order_by('id'))
        Review.objects.bulk_create(
            [
                Review(order=o, user=o.user, stall=self.stall, rating=5, content='味道不错挺香的')
                for o in bulk_orders
            ]
        )
        deep_flagged = self.api.get('/api/v1/ops/reviews?status=flagged').data
        self.assertEqual(deep_flagged['counts']['flagged'], 1)
        self.assertEqual([r['id'] for r in deep_flagged['results']], [old_flagged.pk])
        # Clean up bulk rows so steps 6-8 test exact stall aggregates cleanly
        Review.objects.filter(pk=old_flagged.pk).delete()
        old_flagged_order.delete()
        Review.objects.filter(user__username__startswith='bulk_stu_').delete()
        Order.objects.filter(user__username__startswith='bulk_stu_').delete()
        User.objects.filter(username__startswith='bulk_stu_').delete()

        # 6. Verify public stall rating & count exclude hidden brush reviews ((4 + 1) / 2 = 2.5)
        public_detail = self.api.get(f'/api/v1/stalls/{self.stall.pk}').data
        self.assertEqual(public_detail['review_count'], 2)
        self.assertEqual(public_detail['rating'], 2.5)
        public_ids = {r['id'] for r in public_detail['reviews']}
        self.assertEqual(public_ids, {honest_review.pk, low_review.pk})

        # 7. Verify spammer cannot re-submit review on the hidden order, and merchant cannot reply to hidden review
        self.auth(self.student)
        student_order = self.api.get(f'/api/v1/orders/{brush_reviews[0].order_id}').data
        self.assertTrue(student_order['review']['is_hidden'])
        re_review = self.api.post(
            f'/api/v1/orders/{brush_reviews[0].order_id}/review',
            {'rating': 5, 'content': '再刷一次'},
            format='json',
        )
        self.assertEqual(re_review.status_code, 409)
        self.assertEqual(re_review.data['code'], 'already_reviewed')

        self.auth(self.vendor)
        merchant_list = self.api.get(f'/api/v1/merchant/reviews?stall={self.stall.pk}').data
        self.assertEqual({r['id'] for r in merchant_list}, {honest_review.pk, low_review.pk})
        blocked_reply = self.api.post(
            f'/api/v1/merchant/reviews/{brush_reviews[0].pk}/reply',
            {'content': '谢谢好评'},
            format='json',
        )
        self.assertEqual(blocked_reply.status_code, 400)
        self.assertEqual(blocked_reply.data['code'], 'review_hidden')
        merchant_order_view = self.api.get(f'/api/v1/merchant/orders/{brush_reviews[0].order_id}').data
        self.assertEqual(merchant_order_view['review']['content'], '该评价因违反平台社区规范已被治理屏蔽。')

        # 8. Admin can restore a hidden review with explicit reason
        self.auth(self.admin_user)
        restore_res = self.api.patch(
            f'/api/v1/ops/reviews/{brush_reviews[0].pk}',
            {'action': 'restore', 'moderation_reason': '申诉复核恢复'},
            format='json',
        )
        self.assertEqual(restore_res.status_code, 200, restore_res.data)
        self.assertFalse(restore_res.data['review']['is_hidden'])
        self.assertTrue(AuditLog.objects.filter(action='ops_review_moderated', actor=self.admin_user).exists())
        self.assertTrue(AuditLog.objects.filter(action='ops_review_reply_cleared', actor=self.admin_user).exists())

    def test_admin_self_password_change_audits_and_preserves_session(self):
        staff_acc = User.objects.create_user('ops_pw_tester', password='OldPassword!234', is_staff=True)
        self.api.login(username='ops_pw_tester', password='OldPassword!234')
        res = self.api.post(
            '/api/v1/auth/password',
            {'old_password': 'OldPassword!234', 'new_password': 'NewPassword!567'},
            format='json',
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(self.api.get('/api/v1/ops/overview').status_code, 200)
        self.assertTrue(
            AuditLog.objects.filter(action='password_changed', actor=staff_acc, target=f'auth.User:{staff_acc.pk}').exists()
        )

    def test_demo_stalls_restore_endpoint_and_opt_in_keepalive_preserve_stale_rules(self):
        now = timezone.now()
        demo_stall = Stall.objects.create(
            merchant=self.stall.merchant,
            area=self.stall.area,
            name='示例烤冷面',
            category='小吃',
            is_demo=True,
            is_visible=True,
            accepting_orders=False,
            transaction_enabled=True,
        )
        demo_stall.current_session = BusinessSession.objects.create(
            stall=demo_stall,
            status='closed',
            opened_at=now - timedelta(hours=8),
            last_confirmed_at=now - timedelta(hours=8),
            closes_at=now - timedelta(hours=2),
        )
        demo_stall.save(update_fields=['current_session'])

        paused_demo = Stall.objects.create(
            merchant=self.stall.merchant,
            area=self.stall.area,
            name='示例小馄饨',
            category='正餐',
            is_demo=True,
            is_visible=True,
        )
        paused_demo.current_session = BusinessSession.objects.create(
            stall=paused_demo,
            status='paused',
            opened_at=now - timedelta(hours=3),
            last_confirmed_at=now - timedelta(hours=3),
        )
        paused_demo.save(update_fields=['current_session'])

        null_session_demo = Stall.objects.create(
            merchant=self.stall.merchant,
            area=self.stall.area,
            name='无场次示例摊',
            category='甜品',
            is_demo=True,
            is_visible=True,
            current_session=None,
        )

        hidden_demo = Stall.objects.create(
            merchant=self.stall.merchant,
            area=self.stall.area,
            name='未公开示例摊',
            category='饮品',
            is_demo=True,
            is_visible=False,
        )
        hidden_demo.current_session = BusinessSession.objects.create(
            stall=hidden_demo,
            status='closed',
            opened_at=now - timedelta(hours=5),
            last_confirmed_at=now - timedelta(hours=5),
        )
        hidden_demo.save(update_fields=['current_session'])

        real_stall = Stall.objects.create(
            merchant=self.stall.merchant,
            area=self.stall.area,
            name='真实持证门店',
            category='正餐',
            is_demo=False,
            is_visible=True,
        )
        real_stall.current_session = BusinessSession.objects.create(
            stall=real_stall,
            status='open',
            opened_at=now - timedelta(hours=3),
            last_confirmed_at=now - timedelta(hours=3),
        )
        real_stall.save(update_fields=['current_session'])

        # Unauthorized & read-only staff cannot restore demo stalls
        self.assertIn(self.api.post('/api/v1/ops/demo-stalls/restore', {}, format='json').status_code, (401, 403))
        for actor in (self.student, self.vendor, self.readonly_staff):
            self.auth(actor)
            self.assertEqual(self.api.post('/api/v1/ops/demo-stalls/restore', {}, format='json').status_code, 403)

        # Rejected outside DEMO_MODE
        self.auth(self.admin_user)
        with override_settings(DEMO_MODE=False):
            denied = self.api.post('/api/v1/ops/demo-stalls/restore', {}, format='json')
            self.assertEqual(denied.status_code, 403)
            self.assertEqual(denied.data['code'], 'demo_only')

        # 1-click restore reopens visible demo stalls only (including current_session=None), leaving real & hidden untouched
        restored = self.api.post('/api/v1/ops/demo-stalls/restore', {}, format='json')
        self.assertEqual(restored.status_code, 200, restored.data)
        self.assertIn(demo_stall.pk, restored.data['stall_ids'])
        self.assertIn(paused_demo.pk, restored.data['stall_ids'])
        self.assertIn(null_session_demo.pk, restored.data['stall_ids'])
        self.assertNotIn(hidden_demo.pk, restored.data['stall_ids'])
        self.assertNotIn(real_stall.pk, restored.data['stall_ids'])

        demo_stall.refresh_from_db()
        paused_demo.refresh_from_db()
        null_session_demo.refresh_from_db()
        hidden_demo.refresh_from_db()
        real_stall.refresh_from_db()
        cfg = SiteConfiguration.current()
        self.assertEqual(demo_stall.effective_status(cfg), 'open')
        self.assertTrue(demo_stall.accepting_orders)
        self.assertIsNone(demo_stall.current_session.closes_at)
        self.assertEqual(paused_demo.effective_status(cfg), 'open')
        self.assertEqual(null_session_demo.effective_status(cfg), 'open')
        self.assertEqual(hidden_demo.effective_status(cfg), 'closed')
        self.assertEqual(real_stall.effective_status(cfg), 'stale')
        self.assertTrue(
            AuditLog.objects.filter(
                action='ops_demo_stalls_restored',
                actor=self.admin_user,
                target='market.Stall:demo',
            ).exists()
        )

        # Default expire_orders (without --keep-demo-fresh) STILL enforces location-expiry auto-stop on demo stalls
        demo_stall.current_session.last_confirmed_at = timezone.now() - timedelta(hours=3)
        demo_stall.current_session.save(update_fields=['last_confirmed_at'])
        call_command('expire_orders')
        demo_stall.refresh_from_db()
        self.assertEqual(demo_stall.effective_status(cfg), 'stale')
        self.assertTrue(Event.objects.filter(type='stall_stale', stall=demo_stall).exists())

        # Opt-in --keep-demo-fresh refreshes open demo stalls before expiry, respects paused/closed & real stalls
        paused_demo.current_session.status = 'paused'
        paused_demo.current_session.last_confirmed_at = timezone.now() - timedelta(hours=3)
        paused_demo.current_session.save(update_fields=['status', 'last_confirmed_at'])
        call_command('expire_orders', '--keep-demo-fresh')
        demo_stall.refresh_from_db()
        paused_demo.refresh_from_db()
        real_stall.refresh_from_db()
        self.assertEqual(demo_stall.effective_status(cfg), 'open')
        self.assertEqual(paused_demo.current_session.status, 'paused')
        self.assertEqual(real_stall.effective_status(cfg), 'stale')
        self.assertTrue(Event.objects.filter(type='stall_stale', stall=real_stall).exists())

        # Short stale_minutes (< 30) bypasses both periodic and startup auto-refresh so live expiry demos still trigger stale
        cfg.stale_minutes = 10
        cfg.save()
        demo_stall.current_session.last_confirmed_at = timezone.now() - timedelta(minutes=15)
        demo_stall.current_session.save(update_fields=['last_confirmed_at'])
        call_command('expire_orders', '--keep-demo-fresh')
        self.assertEqual(recover_startup_demo_stalls(), 0)
        demo_stall.refresh_from_db()
        self.assertEqual(demo_stall.effective_status(cfg), 'stale')

        # Startup recovery clears untouched seed_demo 6h closes_at, preserves merchant-set closes_at,
        # and never reopens manually closed/paused demo stalls even when 0 demo stalls are open.
        cfg.stale_minutes = 60
        cfg.save()
        seeded_at = timezone.now() - timedelta(hours=7)
        demo_stall.current_session = BusinessSession.objects.create(
            stall=demo_stall,
            status='open',
            opened_at=seeded_at,
            last_confirmed_at=seeded_at,
            closes_at=seeded_at + timedelta(hours=6),
        )
        demo_stall.save(update_fields=['current_session'])

        merchant_set_at = timezone.now() - timedelta(minutes=10)
        expected_merchant_close = merchant_set_at + timedelta(hours=6)
        null_session_demo.current_session = BusinessSession.objects.create(
            stall=null_session_demo,
            status='open',
            opened_at=merchant_set_at,
            last_confirmed_at=merchant_set_at,
            closes_at=expected_merchant_close,
        )
        null_session_demo.save(update_fields=['current_session'])
        AuditLog.objects.create(actor=self.vendor, action='stall_status', target=str(null_session_demo.pk))

        refreshed = recover_startup_demo_stalls()
        self.assertGreaterEqual(refreshed, 1)
        demo_stall.refresh_from_db()
        null_session_demo.refresh_from_db()
        paused_demo.refresh_from_db()
        self.assertEqual(demo_stall.effective_status(cfg), 'open')
        self.assertIsNone(demo_stall.current_session.closes_at)
        self.assertEqual(null_session_demo.current_session.closes_at, expected_merchant_close)
        self.assertEqual(paused_demo.current_session.status, 'paused')

        # Even if all visible demo stalls are manually closed/paused, startup recovery does not override vendor choices
        for s in Stall.objects.filter(is_demo=True, is_visible=True).select_related('current_session'):
            if s.current_session:
                s.current_session.status = 'closed'
                s.current_session.save(update_fields=['status'])
        self.assertEqual(recover_startup_demo_stalls(), 0)
        demo_stall.refresh_from_db()
        self.assertEqual(demo_stall.effective_status(cfg), 'closed')
