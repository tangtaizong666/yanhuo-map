"""Existing monetary identities and unresolved states survive the schema change."""
import uuid
from datetime import timedelta

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class FinancialHistoryMigrationTests(TransactionTestCase):
    migrate_from = [('market', '0012_security')]
    migrate_to = [('market', '0015_order_history_indexes')]

    def test_original_refund_ids_amounts_modes_and_holds_survive(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        try:
            apps = executor.loader.project_state(self.migrate_from).apps
            User = apps.get_model('auth', 'User')
            student = User.objects.create(username='migration-student')
            vendor = User.objects.create(username='migration-vendor')
            merchant = apps.get_model('market', 'MerchantProfile').objects.create(user=vendor, business_name='历史商家')
            area = apps.get_model('market', 'Area').objects.create(name='校园', latitude=31, longitude=121)
            stall = apps.get_model('market', 'Stall').objects.create(merchant=merchant, area=area,
                name='原摊位', category='小吃', image='/media/merchant-owned-photo.jpg')
            product = apps.get_model('market', 'Product').objects.create(stall=stall, name='原商品', price_cents=800, stock=19)
            now = timezone.now()
            snapshots = []
            for index, (status, mode) in enumerate([('closed', 'live'), ('success', 'simulation')]):
                order = apps.get_model('market', 'Order').objects.create(user=student, stall=stall, stall_name=stall.name,
                    mode=mode, status='cancelled', payment_status='paid' if status == 'closed' else 'refunded',
                    payment_method='wechat', payment_review_required=status == 'closed', total_cents=800,
                    expires_at=now-timedelta(days=1), pickup_address='原位置', pickup_latitude=31,
                    pickup_longitude=121, idempotency_key=uuid.uuid4().hex, request_hash='legacy')
                payment = apps.get_model('market', 'PaymentAttempt').objects.create(order=order, merchant=merchant,
                    mode=mode, channel='native' if mode == 'live' else 'simulation', account_key='original-config',
                    mchid='100000'+str(index), appid='original-app', out_trade_no='ORIGINALTRADE'+str(index),
                    transaction_id='ORIGINALTRANSACTION'+str(index), amount_cents=800, status='paid', expires_at=now)
                refund = apps.get_model('market', 'PaymentRefund').objects.create(order=order, payment=payment,
                    mode=mode, out_refund_no='ORIGINALREFUND'+str(index), refund_id='ORIGINALPROVIDERREFUND'+str(index),
                    amount_cents=800, reason='原申请理由', status=status, completed_at=now if status == 'success' else None)
                snapshots.append((order.pk, payment.pk, refund.pk, status, mode, payment.mchid))
        finally:
            # Leave the test database at the current schema even on fixture failure.
            executor = MigrationExecutor(connection)
            executor.migrate(self.migrate_to)
        apps = executor.loader.project_state(self.migrate_to).apps
        self.assertEqual(apps.get_model('market', 'Product').objects.get(pk=product.pk).stock, 19)
        self.assertEqual(apps.get_model('market', 'Stall').objects.get(pk=stall.pk).image, '/media/merchant-owned-photo.jpg')
        for index, (order_id, payment_id, refund_id, status, mode, mchid) in enumerate(snapshots):
            order = apps.get_model('market', 'Order').objects.get(pk=order_id)
            payment = apps.get_model('market', 'PaymentAttempt').objects.get(pk=payment_id)
            refund = apps.get_model('market', 'PaymentRefund').objects.get(pk=refund_id)
            self.assertEqual((refund.order_id, refund.payment_id, refund.status, refund.mode, refund.amount_cents),
                (order_id, payment_id, status, mode, 800))
            self.assertEqual((refund.out_refund_no, refund.refund_id, refund.mchid),
                ('ORIGINALREFUND'+str(index), 'ORIGINALPROVIDERREFUND'+str(index), mchid))
            self.assertEqual((payment.out_trade_no, payment.transaction_id, payment.account_key),
                ('ORIGINALTRADE'+str(index), 'ORIGINALTRANSACTION'+str(index), 'original-config'))
            self.assertEqual(order.payment_review_required, status == 'closed')
            self.assertEqual(refund.resolved_at is None, status == 'closed')
