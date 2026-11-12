"""Real admin POST boundaries for granting trading permissions to licensed shops."""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .admin import StallAdmissionForm
from .models import MerchantApplication, MerchantProfile, Stall
from .tests import fixtures


@override_settings(DEMO_MODE=False, PRODUCTION=False,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class LicenseAdminTests(TestCase):
    def setUp(self):
        _, self.other, _, self.stall, _ = fixtures()
        self.merchant = self.stall.merchant
        self.operator = User.objects.create_superuser('license-admin', 'admin@example.test', 'Test-only-password')
        self.client.force_login(self.operator)
        Stall.objects.filter(pk=self.stall.pk).update(transaction_enabled=False,
            delivery_approved=False, delivery_enabled=False)
        self.url = reverse('admin:market_stall_change', args=[self.stall.pk])

    def form_data(self, url=None):
        response = self.client.get(url or self.url)
        self.assertEqual(response.status_code, 200)
        form = response.context['adminform'].form
        forms = [form]
        for inline in response.context['inline_admin_formsets']:
            forms += [inline.formset.management_form, *inline.formset.forms]
        data = {'_save': '保存'}
        for rendered in forms:
            for field in rendered:
                value = field.value()
                if getattr(field.field.widget, 'input_type', None) == 'checkbox':
                    if value: data[field.html_name] = 'on'
                elif value is not None:
                    data[field.html_name] = value if isinstance(value, (list, tuple)) else str(value)
        return data

    def invalidate(self, kind):
        changes = {'license_number': 'TEST-ONLY-LICENSE', 'license_valid_until': timezone.localdate()+timedelta(days=1),
            'is_verified': True, 'qualification_tier': 'storefront'}
        changes.update({'number': {'license_number': ''}, 'whitespace': {'license_number': '\u3000\u00a0'},
            'date': {'license_valid_until': None}, 'expired': {'license_valid_until': timezone.localdate()-timedelta(days=1)},
            'verification': {'is_verified': False}, 'mobile': {'qualification_tier': 'mobile_vendor'}}[kind])
        MerchantProfile.objects.filter(pk=self.merchant.pk).update(**changes)

    def assert_rejected(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '不能授予交易或配送权限')
        self.assertTrue(response.context['adminform'].form.non_field_errors())

    def test_each_new_grant_requires_complete_current_qualification(self):
        for kind in ('number', 'whitespace', 'date', 'expired', 'verification', 'mobile'):
            self.invalidate(kind)
            for grant in ('transaction_enabled', 'delivery_approved', 'delivery_enabled'):
                with self.subTest(kind=kind, grant=grant):
                    data = self.form_data()
                    data[grant] = 'on'
                    self.assert_rejected(self.client.post(self.url, data))
                    self.stall.refresh_from_db()
                    self.assertFalse(getattr(self.stall, grant))

    def test_valid_grant_at_expiry_date_and_independent_delivery_approval(self):
        MerchantProfile.objects.filter(pk=self.merchant.pk).update(license_valid_until=timezone.localdate())
        data = self.form_data()
        data['delivery_approved'] = 'on'
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 302, getattr(response, 'context', None))
        self.stall.refresh_from_db()
        self.assertTrue(self.stall.delivery_approved)
        self.assertFalse(self.stall.transaction_enabled)
        data = self.form_data()
        data.update(transaction_enabled='on', delivery_enabled='on')
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.stall.refresh_from_db()
        self.assertTrue(self.stall.transaction_enabled and self.stall.delivery_enabled)

    def test_legacy_invalid_authorizations_can_be_disabled_or_repaired_without_regrant(self):
        self.invalidate('date')
        Stall.objects.filter(pk=self.stall.pk).update(transaction_enabled=True, delivery_approved=True, delivery_enabled=True)
        data = self.form_data()
        data['name'] = '修正展示名称'
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        data = self.form_data()
        data.pop('delivery_enabled')
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.stall.refresh_from_db()
        self.assertTrue(self.stall.transaction_enabled)
        self.assertFalse(self.stall.delivery_enabled)
        data = self.form_data()
        data.pop('transaction_enabled')
        data.pop('delivery_approved')
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.stall.refresh_from_db()
        self.assertFalse(self.stall.transaction_enabled or self.stall.delivery_approved or self.stall.delivery_enabled)

    @override_settings(DEMO_MODE=True)
    def test_demo_may_rehearse_but_cannot_become_live_with_retained_permissions(self):
        self.invalidate('date')
        Stall.objects.filter(pk=self.stall.pk).update(is_demo=True)
        data = self.form_data()
        data.update(transaction_enabled='on', delivery_approved='on')
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        data = self.form_data()
        data.pop('is_demo')
        self.assert_rejected(self.client.post(self.url, data))
        self.stall.refresh_from_db()
        self.assertTrue(self.stall.is_demo)
        # Converting to a real information-only record is a valid reduction.
        data.pop('transaction_enabled')
        data.pop('delivery_approved')
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.stall.refresh_from_db()
        self.assertFalse(self.stall.is_demo or self.stall.transaction_enabled or self.stall.delivery_approved)

    def test_reassigning_authorized_stall_checks_the_new_merchant(self):
        unqualified = MerchantProfile.objects.create(user=self.other, business_name='尚未核验的商户')
        Stall.objects.filter(pk=self.stall.pk).update(transaction_enabled=True)
        data = self.form_data()
        data['merchant'] = str(unqualified.pk)
        self.assert_rejected(self.client.post(self.url, data))
        self.stall.refresh_from_db()
        self.assertEqual(self.stall.merchant_id, self.merchant.pk)
        data.pop('transaction_enabled')
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.stall.refresh_from_db()
        self.assertEqual(self.stall.merchant_id, unqualified.pk)
        self.assertFalse(self.stall.transaction_enabled)

    def test_invalid_new_stall_grant_returns_form_errors_not_a_server_error(self):
        self.invalidate('number')
        url = reverse('admin:market_stall_add')
        data = self.form_data(url)
        data.update(merchant=str(self.merchant.pk), area=str(self.stall.area_id), name='待核验新门店',
            category='小吃', transaction_enabled='on')
        self.assert_rejected(self.client.post(url, data))
        self.assertFalse(Stall.objects.filter(name='待核验新门店').exists())

    def test_qualification_is_reread_after_model_choice_cleaning(self):
        data = self.form_data()
        data['transaction_enabled'] = 'on'
        original = StallAdmissionForm.clean
        def changed_before_clean(form):
            # ModelChoiceField has already loaded a valid but now stale profile.
            self.assertTrue(form.cleaned_data['merchant'].license_number)
            MerchantProfile.objects.filter(pk=self.merchant.pk).update(license_number='')
            return original(form)
        with patch.object(StallAdmissionForm, 'clean', changed_before_clean):
            self.assert_rejected(self.client.post(self.url, data))
        self.stall.refresh_from_db()
        self.assertFalse(self.stall.transaction_enabled)

    def test_admin_approval_creates_information_shell_without_trade_permissions(self):
        application = MerchantApplication.objects.create(user=self.other, business_name='试点申请主体',
            stall_name='试点新摊位', contact_phone='13800138000', area=self.stall.area, category='小吃',
            address_note='南门现场待确认', status='submitted', confirmed_at=timezone.now())
        response = self.client.post(reverse('admin:market_merchantapplication_changelist'),
            {'action': 'approve', '_selected_action': [application.pk], 'index': '0'})
        self.assertEqual(response.status_code, 302)
        application.refresh_from_db()
        self.assertEqual(application.status, 'approved')
        stall = application.approved_stall
        self.assertFalse(stall.is_visible or stall.transaction_enabled or stall.delivery_approved or stall.delivery_enabled)
        self.assertFalse(stall.merchant.is_verified)

    def test_reassignment_validates_permissions_enabled_after_form_initialization(self):
        unqualified = MerchantProfile.objects.create(user=self.other, business_name='未核验的新主体')
        data = self.form_data()
        data['merchant'] = str(unqualified.pk)
        original = StallAdmissionForm.clean
        def concurrent_grant(form):
            self.assertNotIn('transaction_enabled', form.changed_data)
            Stall.objects.filter(pk=self.stall.pk).update(transaction_enabled=True)
            return original(form)
        with patch.object(StallAdmissionForm, 'clean', concurrent_grant):
            self.assert_rejected(self.client.post(self.url, data))
        self.stall.refresh_from_db()
        self.assertEqual(self.stall.merchant_id, self.merchant.pk)
        self.assertTrue(self.stall.transaction_enabled)

    def test_merchant_admin_cannot_verify_a_storefront_missing_number_or_date(self):
        url = reverse('admin:market_merchantprofile_change', args=[self.merchant.pk])
        for field, value in (('license_number', ''), ('license_valid_until', None)):
            with self.subTest(field=field):
                MerchantProfile.objects.filter(pk=self.merchant.pk).update(is_verified=False,
                    license_number='TEST-ONLY-LICENSE', license_valid_until=timezone.localdate())
                MerchantProfile.objects.filter(pk=self.merchant.pk).update(**{field: value})
                data = self.form_data(url)
                data['is_verified'] = 'on'
                response = self.client.post(url, data)
                self.assertEqual(response.status_code, 200)
                self.assertIn(field, response.context['adminform'].form.errors)
                self.merchant.refresh_from_db()
                self.assertFalse(self.merchant.is_verified)

    def test_merchant_admin_can_save_incomplete_unverified_draft_and_revoke_legacy_verification(self):
        url = reverse('admin:market_merchantprofile_change', args=[self.merchant.pk])
        MerchantProfile.objects.filter(pk=self.merchant.pk).update(is_verified=False,
            license_number='', license_valid_until=None)
        data = self.form_data(url)
        data['business_name'] = '正在补充证照的门店'
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.merchant.refresh_from_db()
        self.assertEqual(self.merchant.business_name, '正在补充证照的门店')
        self.assertFalse(self.merchant.is_verified)
        # Simulate a legacy record that was verified before completeness rules.
        MerchantProfile.objects.filter(pk=self.merchant.pk).update(is_verified=True)
        data = self.form_data(url)
        data.pop('is_verified')
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.merchant.refresh_from_db()
        self.assertFalse(self.merchant.is_verified)
        self.assertEqual(self.merchant.license_number, '')
        self.assertIsNone(self.merchant.license_valid_until)
