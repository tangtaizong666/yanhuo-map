"""Order preferences and preparation estimates use isolated fixture databases."""
import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.db import close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .errors import BusinessError
from .models import AuditLog, Order, PreparationRequest, Product
from .serializers import OrderInput
from .services import cancel_order, create_order, merchant_action
from .tests import fixtures, payload


@override_settings(DEMO_MODE=True, SERVICES_SIMULATION_ENABLED=False)
class TastesPreparationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.student, cls.other, cls.vendor, cls.stall, cls.product = fixtures()
        cls.tastes = [{'name': '辣度', 'choices': ['不辣', '微辣', '加辣']}, {'name': '香菜', 'choices': ['要', '不要']}]
        cls.product.taste_options = cls.tastes
        cls.product.save()

    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(self.student)

    def portions(self):
        return [{'options': {'辣度': '不辣'}, 'note': '分开装'}, {'options': {'辣度': '加辣', '香菜': '不要'}, 'note': ''}]

    def data(self, **overrides):
        values = payload(self.stall, self.product, quantity=2)
        values['items'][0]['portions'] = self.portions()
        return {**values, **overrides}

    def create(self, data=None):
        response = self.api.post('/api/v1/orders', data or self.data(), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return Order.objects.get(pk=response.data['id'])

    def action(self, order, action, **fields):
        self.api.force_authenticate(self.vendor)
        return self.api.post(f'/api/v1/merchant/orders/{order.pk}/action', {'action': action, **fields}, format='json')

    def test_catalogue_roundtrips_optional_free_tastes_and_normalizes_whitespace(self):
        self.api.force_authenticate(self.vendor)
        data = {'name': '新餐点', 'price_cents': 900,
            'taste_options': [{'name': ' 辣度 ', 'choices': [' 不辣 ', '微辣']}]}
        response = self.api.post(f'/api/v1/merchant/stalls/{self.stall.pk}/products', data, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['taste_options'], [{'name': '辣度', 'choices': ['不辣', '微辣']}])
        public = self.api.get(f'/api/v1/stalls/{self.stall.pk}').data
        self.assertEqual(next(p for p in public['products'] if p['id'] == response.data['id'])['taste_options'], response.data['taste_options'])
        minimal = self.api.post(f'/api/v1/merchant/stalls/{self.stall.pk}/products', {'name': '无选项', 'price_cents': 500}, format='json')
        self.assertEqual(minimal.data['taste_options'], [])

    def test_tastes_reject_duplicates_oversized_and_paid_options_without_modifying_catalogue(self):
        self.api.force_authenticate(self.vendor)
        invalid = [None, {}, self.tastes * 2, [{'name': '', 'choices': ['不辣']}],
            [{'name': '辣度', 'choices': []}], [{'name': '辣度', 'choices': ['微辣', ' 微辣 ']}],
            [{'name': '辣度', 'choices': ['不辣'], 'price_cents': 100}],
            [{'name': '辣度', 'choices': list('123456789')}], [{'name': '辣度', 'choices': [1]}],
            [{'name': '长' * 21, 'choices': ['不辣']}], [self.tastes[0], self.tastes[0]]]
        for tastes in invalid:
            response = self.api.patch(f'/api/v1/merchant/products/{self.product.pk}', {'taste_options': tastes}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        self.product.refresh_from_db()
        self.assertEqual(self.product.taste_options, self.tastes)

    def test_other_account_cannot_edit_product_tastes(self):
        self.api.force_authenticate(self.other)
        response = self.api.patch(f'/api/v1/merchant/products/{self.product.pk}', {'taste_options': []}, format='json')
        self.assertEqual(response.status_code, 404)

    def test_each_portion_is_snapshotted_with_total_inventory_and_price(self):
        order = self.create()
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(order.items.get().portions, self.portions())
        self.assertEqual((order.items.get().quantity, order.total_cents), (2, 1600))
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 3)
        Product.objects.filter(pk=self.product.pk).update(taste_options=[], name='改名', price_cents=1000)
        result = self.api.get(f'/api/v1/orders/{order.pk}').data
        self.assertEqual(result['items'][0]['portions'], self.portions())
        self.assertEqual(result['items'][0]['unit_price_cents'], 800)
        self.assertEqual(result['items'][0]['name'], '招牌烤冷面')

    def test_unknown_tastes_return_conflict_without_reserving_stock(self):
        for options in ({'辣度': '超辣'}, {'甜度': '少糖'}):
            data = self.data()
            data['items'][0]['portions'][0]['options'] = options
            result = self.api.post('/api/v1/orders', data, format='json')
            self.assertEqual((result.status_code, result.data['code'], result.data['product_id']), (409, 'tastes_changed', self.product.pk))
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 5)
        self.assertEqual(Order.objects.count(), 0)

    def test_per_portion_input_requires_matching_quantity_and_limited_plain_text(self):
        invalid = [[], self.portions()[:1], self.portions() * 2,
            [{'options': {}, 'note': 'a' * 101}, {}], [{'options': [], 'note': ''}, {}],
            [{'options': {'辣度': 1}}, {}], [{'options': {}, 'price_cents': 10}, {}],
            [{'options': {'辣度': '不辣', ' 辣度 ': '加辣'}}, {}]]
        for portions in invalid:
            data = self.data()
            data['items'][0]['portions'] = portions
            self.assertEqual(self.api.post('/api/v1/orders', data, format='json').status_code, 400)
        self.assertEqual(Order.objects.count(), 0)

    def test_preferences_are_optional_and_duplicate_product_lines_remain_rejected(self):
        data = self.data()
        data['items'][0]['portions'] = [{}, {'note': ' 少盐 '}]
        order = self.create(data)
        self.assertEqual(order.items.get().portions, [{'options': {}, 'note': ''}, {'options': {}, 'note': '少盐'}])
        data = self.data()
        data['items'].append(data['items'][0].copy())
        self.assertEqual(self.api.post('/api/v1/orders', data, format='json').status_code, 400)

    def test_retry_uses_original_snapshot_even_if_menu_options_changed(self):
        data = self.data()
        order = self.create(data)
        Product.objects.filter(pk=self.product.pk).update(taste_options=[])
        repeated = self.api.post('/api/v1/orders', data, format='json')
        self.assertEqual((repeated.status_code, repeated.data['id']), (200, str(order.pk)))
        self.assertEqual(repeated.data['items'][0]['portions'], self.portions())
        changed = self.data(idempotency_key=data['idempotency_key'])
        changed['items'][0]['portions'][0]['note'] = '换一份'
        self.assertEqual(self.api.post('/api/v1/orders', changed, format='json').data['code'], 'idempotency_conflict')

    def test_pre_feature_hash_and_explicit_empty_portions_are_compatible(self):
        data = payload(self.stall, self.product)
        form = OrderInput(data=data)
        form.is_valid(raise_exception=True)
        legacy = {**form.validated_data, 'items': sorted(form.validated_data['items'], key=lambda row: row['product_id'])}
        old_hash = hashlib.sha256(json.dumps(legacy, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        order = self.create(data)
        self.assertEqual(order.request_hash, old_hash)
        data['items'][0]['portions'] = [{'options': {}, 'note': ''}]
        repeated = self.api.post('/api/v1/orders', data, format='json')
        self.assertEqual((repeated.status_code, repeated.data['id']), (200, str(order.pk)))
        self.assertEqual(repeated.data['items'][0]['portions'], [])

    def test_cancellation_restores_total_quantity_once_without_erasing_portions(self):
        order = self.create()
        cancel_order(order.pk, self.student, '')
        cancel_order(order.pk, self.student, '')
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 5)
        self.assertEqual(order.items.get().portions, self.portions())

    def test_accept_populates_estimate_with_default_and_student_can_read_it(self):
        order = self.create()
        result = self.action(order, 'accept')
        self.assertEqual(result.status_code, 200, result.data)
        order.refresh_from_db()
        self.assertEqual(order.estimated_ready_at, order.accepted_at + timedelta(minutes=self.stall.prep_minutes))
        self.assertEqual(order.prep_updated_at, order.accepted_at)
        self.api.force_authenticate(self.student)
        student = self.api.get(f'/api/v1/orders/{order.pk}').data
        self.assertEqual(student['estimated_ready_at'], result.data['estimated_ready_at'])
        self.assertEqual(student['prep_delay_reason'], '')

    def test_accept_custom_minutes_and_key_retry_never_reset_eta(self):
        order = self.create()
        key = uuid.uuid4().hex
        first = self.action(order, 'accept', prep_minutes=25, idempotency_key=key)
        self.assertEqual(first.status_code, 200, first.data)
        with patch('market.services.timezone.now', return_value=timezone.now()+timedelta(minutes=7)):
            again = self.action(order, 'accept', prep_minutes=25, idempotency_key=key)
        self.assertEqual(again.data['estimated_ready_at'], first.data['estimated_ready_at'])
        order.refresh_from_db()
        self.assertEqual(order.estimated_ready_at-order.accepted_at, timedelta(minutes=25))
        self.assertEqual(PreparationRequest.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(action='order_accept').count(), 1)
        self.assertEqual(self.action(order, 'accept', prep_minutes=30, idempotency_key=key).data['code'], 'idempotency_conflict')
        self.assertEqual(self.action(order, 'accept', prep_minutes=25).data['code'], 'invalid_transition')

    def test_update_estimate_is_required_reason_and_idempotent_not_a_new_order_state(self):
        order = self.create()
        self.action(order, 'accept')
        key = uuid.uuid4().hex
        first = self.action(order, 'update_prep', prep_minutes=15, reason='还需等这一锅出餐', idempotency_key=key)
        self.assertEqual(first.status_code, 200, first.data)
        order.refresh_from_db()
        self.assertEqual(order.estimated_ready_at-order.prep_updated_at, timedelta(minutes=15))
        self.assertEqual((order.status, order.payment_status, order.prep_delay_reason), ('preparing', 'unpaid', '还需等这一锅出餐'))
        with patch('market.services.timezone.now', return_value=timezone.now()+timedelta(minutes=5)):
            again = self.action(order, 'update_prep', prep_minutes=15, reason='还需等这一锅出餐', idempotency_key=key)
        self.assertEqual(again.data['estimated_ready_at'], first.data['estimated_ready_at'])
        self.assertEqual(AuditLog.objects.filter(action='order_update_prep').count(), 1)
        self.assertEqual(self.action(order, 'update_prep', prep_minutes=16, reason='换时间', idempotency_key=key).data['code'], 'idempotency_conflict')

    def test_preparation_validation_rejects_out_of_bounds_missing_reason_and_key(self):
        order = self.create()
        for minutes in (0, 181, 'abc', None):
            result = self.action(order, 'accept', prep_minutes=minutes)
            self.assertEqual(result.status_code, 400)
        self.action(order, 'accept')
        for fields in ({'prep_minutes': 10}, {'prep_minutes': 10, 'reason': '忙碌'},
                       {'prep_minutes': 10, 'reason': ' ', 'idempotency_key': uuid.uuid4().hex},
                       {'prep_minutes': 10, 'reason': '忙碌', 'idempotency_key': 'short'}):
            self.assertEqual(self.action(order, 'update_prep', **fields).status_code, 400)
        self.assertEqual(PreparationRequest.objects.count(), 0)

    def test_preparation_updates_require_owner_and_preparing_without_pending_cancellation(self):
        order = self.create()
        fields = {'prep_minutes': 5, 'reason': '新预估', 'idempotency_key': uuid.uuid4().hex}
        self.assertEqual(self.action(order, 'update_prep', **fields).data['code'], 'invalid_transition')
        self.action(order, 'accept')
        for user in (self.student, self.other):
            self.api.force_authenticate(user)
            result = self.api.post(f'/api/v1/merchant/orders/{order.pk}/action', {'action': 'update_prep', **fields}, format='json')
            self.assertEqual(result.status_code, 404)
        cancel_order(order.pk, self.student, '临时有事')
        self.assertEqual(self.action(order, 'update_prep', **fields).data['code'], 'invalid_transition')
        self.action(order, 'deny_cancel')
        self.action(order, 'ready')
        self.assertEqual(self.action(order, 'update_prep', **fields).data['code'], 'invalid_transition')

    def test_prep_replay_after_ready_returns_current_order_without_undoing_ready(self):
        order = self.create()
        self.action(order, 'accept')
        fields = {'prep_minutes': 5, 'reason': '新预估', 'idempotency_key': uuid.uuid4().hex}
        first = self.action(order, 'update_prep', **fields)
        self.action(order, 'ready')
        replay = self.action(order, 'update_prep', **fields)
        self.assertEqual((replay.status_code, replay.data['status']), (200, 'ready'))
        self.assertEqual(replay.data['estimated_ready_at'], first.data['estimated_ready_at'])
        self.action(order, 'confirm_payment')
        self.assertEqual(self.action(order, 'complete', pickup_code=order.pickup_code).data['status'], 'completed')

    def test_payment_review_blocks_estimate_change_without_changing_money_or_stock(self):
        order = self.create()
        self.action(order, 'accept')
        order.refresh_from_db()
        original_estimate = order.estimated_ready_at
        Order.objects.filter(pk=order.pk).update(payment_review_required=True)
        result = self.action(order, 'update_prep', prep_minutes=15, reason='等一锅', idempotency_key='payment-review-check')
        self.assertEqual(result.data['code'], 'payment_requires_review')
        order.refresh_from_db()
        self.assertEqual((order.estimated_ready_at, order.total_cents, order.payment_status), (original_estimate, 1600, 'unpaid'))
        self.assertEqual(PreparationRequest.objects.count(), 0)
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 3)


@skipUnless(connection.vendor == 'postgresql', 'Row-lock races require PostgreSQL')
class TastesPreparationConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.student, self.other, self.vendor, self.stall, self.product = fixtures()

    def race(self, first, second):
        gate = Barrier(2)
        def run(task):
            close_old_connections()
            try:
                gate.wait(timeout=10)
                return task()
            finally: connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = pool.submit(run, first), pool.submit(run, second)
            return a.result(timeout=30), b.result(timeout=30)

    def action(self, order, action, **fields):
        try:
            result = merchant_action(order.pk, self.vendor, action, **fields)
            return ('ok', result.estimated_ready_at)
        except BusinessError as exc: return (exc.detail['code'], None)

    def test_same_accept_request_has_one_estimate_and_audit(self):
        order, _ = create_order(self.student, payload(self.stall, self.product))
        task = lambda: self.action(order, 'accept', prep_minutes=20, idempotency_key='same-accept-request')
        a, b = self.race(task, task)
        self.assertEqual(a, b)
        self.assertEqual(a[0], 'ok')
        self.assertEqual(PreparationRequest.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(action='order_accept').count(), 1)

    def test_same_update_request_changes_estimate_only_once(self):
        order, _ = create_order(self.student, payload(self.stall, self.product))
        merchant_action(order.pk, self.vendor, 'accept')
        task = lambda: self.action(order, 'update_prep', reason='等一锅', prep_minutes=15, idempotency_key='same-update-request')
        a, b = self.race(task, task)
        self.assertEqual(a, b)
        self.assertEqual(a[0], 'ok')
        self.assertEqual(PreparationRequest.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(action='order_update_prep').count(), 1)

    def test_ready_and_estimate_race_cannot_revert_ready(self):
        order, _ = create_order(self.student, payload(self.stall, self.product))
        merchant_action(order.pk, self.vendor, 'accept')
        a, b = self.race(lambda: self.action(order, 'ready'), lambda: self.action(order, 'update_prep',
            reason='快好了', prep_minutes=5, idempotency_key='ready-update-race'))
        self.assertEqual(a[0], 'ok')
        self.assertIn(b[0], ('ok', 'invalid_transition'))
        order.refresh_from_db()
        self.assertEqual(order.status, 'ready')

    def test_last_stock_with_different_portion_notes_has_one_winner(self):
        Product.objects.filter(pk=self.product.pk).update(stock=1)
        data_a, data_b = payload(self.stall, self.product), payload(self.stall, self.product)
        data_a['items'][0]['portions'] = [{'options': {}, 'note': '少盐'}]
        data_b['items'][0]['portions'] = [{'options': {}, 'note': '多盐'}]
        def place(user, data):
            try: return ('created', create_order(user, data)[0].pk)
            except BusinessError as exc: return (exc.detail['code'], None)
        a, b = self.race(lambda: place(self.student, data_a), lambda: place(self.other, data_b))
        self.assertCountEqual([a[0], b[0]], ['created', 'out_of_stock'])
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(Product.objects.get(pk=self.product.pk).stock, 0)
        self.assertIn(Order.objects.get().items.get().portions[0]['note'], ('少盐', '多盐'))

    def test_menu_edit_and_checkout_serialize_without_losing_the_chosen_snapshot(self):
        Product.objects.filter(pk=self.product.pk).update(taste_options=[{'name': '辣度', 'choices': ['不辣']}])
        data = payload(self.stall, self.product)
        data['items'][0]['portions'] = [{'options': {'辣度': '不辣'}, 'note': ''}]
        def order():
            try: return ('created', create_order(self.student, data)[0].pk)
            except BusinessError as exc: return (exc.detail['code'], None)
        def edit():
            api = APIClient()
            api.force_authenticate(self.vendor)
            return api.patch(f'/api/v1/merchant/products/{self.product.pk}', {'taste_options': []}, format='json').status_code
        result, status = self.race(order, edit)
        self.assertEqual(status, 200)
        self.assertIn(result[0], ('created', 'tastes_changed'))
        self.product.refresh_from_db()
        self.assertEqual(self.product.taste_options, [])
        if result[0] == 'created':
            self.assertEqual(Order.objects.get().items.get().portions, data['items'][0]['portions'])
            self.assertEqual(self.product.stock, 4)
        else:
            self.assertFalse(Order.objects.exists())
            self.assertEqual(self.product.stock, 5)
