"""Missing licence facts never grant real trade; historical orders stay operable."""
from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .admission import ADMISSION_WHITESPACE, has_admission_text, new_trade_eligibility_q, pickup_eligibility_reason
from .models import MerchantProfile, Order, Product, Stall
from .tests import fixtures, payload


@override_settings(DEMO_MODE=False, SERVICES_SIMULATION_ENABLED=False, PRODUCTION=False,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class LicenseAdmissionTests(TestCase):
    def setUp(self):
        self.student, self.other, self.vendor, self.stall, self.product = fixtures()
        self.merchant = self.stall.merchant
        self.today = timezone.localdate()
        self.client = APIClient()
        self.client.force_authenticate(self.student)

    def save_licence(self, number='TEST-ONLY-LICENSE', valid_until=None):
        # Deliberately bypass full_clean to model legacy/operator-imported rows.
        self.merchant.license_number = number
        self.merchant.license_valid_until = valid_until
        self.merchant.save(update_fields=['license_number', 'license_valid_until'])

    def assert_admission_matches_sql(self, expected):
        self.stall.refresh_from_db()
        self.assertEqual(not pickup_eligibility_reason(self.stall), expected)
        self.assertEqual(Stall.objects.filter(new_trade_eligibility_q(), pk=self.stall.pk).exists(), expected)
        self.assertEqual(Product.objects.filter(new_trade_eligibility_q('stall__'), pk=self.product.pk).exists(), expected)

    def post_order(self, data=None):
        return self.client.post('/api/v1/orders', data or payload(self.stall, self.product), format='json')

    def test_unicode_whitespace_is_shared_by_python_sql_and_prefixed_sql(self):
        python_whitespace = {chr(code) for code in range(0x110000) if chr(code).isspace()}
        self.assertEqual(set(ADMISSION_WHITESPACE), python_whitespace)
        blanks = ['', *sorted(python_whitespace), ADMISSION_WHITESPACE]
        self.assertFalse(has_admission_text(None))
        for value in blanks:
            with self.subTest(value=repr(value)):
                self.assertFalse(has_admission_text(value))
                self.save_licence(value, self.today)
                self.assert_admission_matches_sql(False)
                self.save_licence(value+'TEST-ONLY'+value, self.today)
                self.assert_admission_matches_sql(True)

    def test_address_blank_rules_match_licence_rules_in_both_sql_paths(self):
        self.save_licence(valid_until=self.today)
        for field in ('licensed_business_address', 'food_preparation_address'):
            original = getattr(self.merchant, field)
            for blank in (' ', '\t\n', '\u3000', '\xa0', '\x1c', '\u0085', ADMISSION_WHITESPACE):
                with self.subTest(field=field, blank=repr(blank)):
                    # Nonempty whitespace also exercises old DB rows that satisfy
                    # the original address-not-empty database constraint.
                    setattr(self.merchant, field, blank)
                    self.merchant.save(update_fields=[field])
                    self.assert_admission_matches_sql(False)
                    setattr(self.merchant, field, blank+'测试地址'+blank)
                    self.merchant.save(update_fields=[field])
                    self.assert_admission_matches_sql(True)
            setattr(self.merchant, field, original)
            self.merchant.save(update_fields=[field])

    def test_unknown_and_expired_dates_fail_but_today_and_future_are_valid(self):
        for expiry, expected in ((None, False), (self.today-timedelta(days=1), False),
                (self.today, True), (self.today+timedelta(days=1), True)):
            with self.subTest(expiry=expiry):
                self.save_licence(valid_until=expiry)
                self.assert_admission_matches_sql(expected)
        self.save_licence(valid_until=self.today)
        with patch('market.admission.timezone.localdate', return_value=self.today+timedelta(days=1)):
            self.assert_admission_matches_sql(False)

    def test_model_blocks_verifying_missing_or_expired_licence(self):
        for number, expiry, field in (('', self.today, 'license_number'),
                ('\u3000\xa0\n', self.today, 'license_number'),
                ('TEST-ONLY', None, 'license_valid_until'),
                ('TEST-ONLY', self.today-timedelta(days=1), 'license_valid_until')):
            with self.subTest(number=repr(number), expiry=expiry):
                self.merchant.license_number = number
                self.merchant.license_valid_until = expiry
                with self.assertRaises(ValidationError) as error:
                    self.merchant.full_clean()
                self.assertIn(field, error.exception.message_dict)
        self.merchant.license_number, self.merchant.license_valid_until = 'TEST-ONLY', self.today
        self.merchant.full_clean()

    def test_unverified_draft_and_revocation_keep_missing_licence_editable(self):
        for expiry in (None, self.today-timedelta(days=1)):
            with self.subTest(expiry=expiry):
                self.merchant.is_verified = False
                self.merchant.license_number = ''
                self.merchant.license_valid_until = expiry
                self.merchant.full_clean()
                self.merchant.save(update_fields=['is_verified', 'license_number', 'license_valid_until'])
                self.assert_admission_matches_sql(False)
        draft = MerchantProfile(user=self.other, business_name='未核验测试草稿',
            qualification_tier='storefront', licensed_business_address='测试门店', food_preparation_address='测试后厨')
        draft.full_clean()
        draft.save()
        self.assertFalse(draft.is_verified)
        self.assertEqual(draft.license_number, '')
        self.assertIsNone(draft.license_valid_until)

    def test_missing_licence_keeps_information_but_disables_new_trade_everywhere(self):
        self.product.stock = 0
        self.product.display_availability = 'available'
        self.product.save(update_fields=['stock', 'display_availability'])
        for number, expiry in (('', self.today), ('\u3000\xa0', self.today), ('TEST-ONLY', None), ('', None)):
            with self.subTest(number=repr(number), expiry=expiry):
                self.save_licence(number, expiry)
                detail = self.client.get(f'/api/v1/stalls/{self.stall.pk}')
                self.assertEqual(detail.status_code, 200, detail.data)
                self.assertTrue(detail.data['capabilities']['public_listing']['available'])
                self.assertFalse(detail.data['transaction_enabled'])
                self.assertFalse(detail.data['can_order'])
                for name in ('pickup_orders', 'online_payment', 'delivery_orders'):
                    self.assertFalse(detail.data['capabilities'][name]['eligible'])
                    self.assertFalse(detail.data['capabilities'][name]['available'])
                self.assertFalse(detail.data['wechat_payment']['available'])
                self.assertEqual(detail.data['wechat_payment']['channels'], [])
                dish = detail.data['products'][0]
                self.assertEqual((dish['availability'], dish['max_order_quantity'], dish['display_only']), ('available', 0, True))
                rows = self.client.get('/api/v1/stalls').data['results']
                self.assertEqual([row['id'] for row in rows], [self.stall.pk])
                self.assertFalse(rows[0]['can_order'])
                self.assertEqual(rows[0]['products'][0]['id'], self.product.pk)
                self.assertEqual(self.client.get('/api/v1/stalls', {'status': 'orderable'}).data['results'], [])
                self.assertEqual(self.client.get('/api/v1/products').data['results'][0]['product']['id'], self.product.pk)
                mapped = self.client.get('/api/v1/stalls/map').data['results']
                self.assertEqual([row['id'] for row in mapped], [self.stall.pk])
                self.assertFalse(mapped[0]['can_order'])

    def test_missing_licence_rejects_actual_pickup_and_delivery_without_reserving_stock(self):
        for number, expiry in (('', self.today), ('\u3000', self.today), ('TEST-ONLY', None), ('', None)):
            self.save_licence(number, expiry)
            for fulfillment in ('pickup', 'delivery'):
                with self.subTest(number=repr(number), expiry=expiry, fulfillment=fulfillment):
                    data = payload(self.stall, self.product)
                    if fulfillment == 'delivery':
                        data.update(fulfillment_type='delivery', delivery_point_id=999,
                            expected_delivery_fee_cents=300, recipient_name='测试同学', contact_phone='13800000000')
                    response = self.post_order(data)
                    self.assertEqual(response.status_code, 409, response.data)
                    self.assertEqual(response.data['code'], 'stall_unavailable')
                    self.assertIn('许可证', response.data['detail'])
            self.assertFalse(Order.objects.exists())
            self.product.refresh_from_db()
            self.assertEqual((self.product.stock, self.product.stock_version), (5, 0))

    def test_missing_licence_blocks_enabling_services_but_allows_switching_them_off(self):
        self.save_licence('', None)
        self.client.force_authenticate(self.vendor)
        for path, key in (('profile', 'accepting_orders'), ('delivery', 'enabled')):
            url = f'/api/v1/merchant/stalls/{self.stall.pk}/{path}'
            self.assertEqual(self.client.patch(url, {key: True}, format='json').status_code, 409)
            disabled = self.client.patch(url, {key: False}, format='json')
            self.assertEqual(disabled.status_code, 200, disabled.data)
        services = self.client.get(f'/api/v1/merchant/stalls/{self.stall.pk}/services').data
        self.assertFalse(services['capabilities']['pickup_orders']['eligible'])

    def test_existing_order_replay_view_and_cancellation_survive_missing_licence(self):
        data = payload(self.stall, self.product)
        created = self.post_order(data)
        self.assertEqual(created.status_code, 201, created.data)
        self.save_licence('', None)
        replay = self.post_order(data)
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertEqual(replay.data['id'], created.data['id'])
        self.assertEqual(Order.objects.count(), 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 4)
        order_url = f"/api/v1/orders/{created.data['id']}"
        self.assertEqual(self.client.get(order_url).status_code, 200)
        cancelled = self.client.post(order_url+'/cancel', {}, format='json')
        self.assertEqual(cancelled.status_code, 200, cancelled.data)
        self.assertEqual(cancelled.data['status'], 'cancelled')
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 5)

    def test_existing_order_can_still_be_fulfilled_after_licence_becomes_missing(self):
        created = self.post_order()
        self.assertEqual(created.status_code, 201, created.data)
        order = Order.objects.get(pk=created.data['id'])
        self.save_licence('', None)
        self.client.force_authenticate(self.vendor)
        for action in ('accept', 'ready', 'confirm_payment', 'complete'):
            result = self.client.post(f'/api/v1/merchant/orders/{order.pk}/action',
                {'action': action, 'pickup_code': order.pickup_code}, format='json')
            self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual(result.data['status'], 'completed')

    def test_real_checkout_accepts_expiry_today_and_rejects_next_day(self):
        self.save_licence(valid_until=self.today)
        self.assertEqual(self.post_order().status_code, 201)
        self.client.force_authenticate(self.other)
        with patch('market.admission.timezone.localdate', return_value=self.today+timedelta(days=1)):
            result = self.post_order()
            self.assertEqual(result.status_code, 409, result.data)
            self.assertIn('有效期', result.data['detail'])
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 4)

    @override_settings(DEMO_MODE=True, PRODUCTION=False)
    def test_explicit_demo_keeps_its_own_rule_but_real_stalls_cannot_borrow_it(self):
        self.save_licence('', None)
        self.assert_admission_matches_sql(False)
        self.assertEqual(self.post_order().status_code, 409)
        self.stall.is_demo = True
        self.stall.save(update_fields=['is_demo'])
        self.assert_admission_matches_sql(True)
        simulated = self.post_order()
        self.assertEqual(simulated.status_code, 201, simulated.data)
        self.assertEqual(simulated.data['mode'], 'simulation')
        with override_settings(DEMO_MODE=False):
            self.assert_admission_matches_sql(False)
        with override_settings(PRODUCTION=True):
            self.assert_admission_matches_sql(False)
