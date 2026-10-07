"""Synthetic terminal money history for the marked runtime backup drill only.

This is a test program, never an API or production fault/configuration switch.
The host runner must verify all Compose project/run/directory labels first.
"""
from datetime import timedelta
import hashlib
import json
import re

from django.conf import settings
from django.contrib.auth.models import User
from django.db import connection, transaction
from django.utils import timezone


def seed_runtime_backup_fixture(run_id):
    if (connection.vendor != 'postgresql' or connection.settings_dict['NAME'] != 'yanhuo_runtime'
            or settings.WECHAT_PAY_ENABLED or settings.WECHAT_PAY_CONFIG_FILE
            or not re.fullmatch(r'[a-f0-9]{12}', run_id)):
        raise RuntimeError('Runtime restore fixtures require an isolated database and disabled real payments')
    from market.financial import evidence
    from market.financial_models import FinancialEvidence
    from market.models import Order, OrderItem, PaymentAttempt, PaymentRefund, PaymentNotification, Stall

    with transaction.atomic():
        now = timezone.now()
        stall = Stall.objects.get(name='测试烤冷面')
        product = stall.products.order_by('pk').first()
        user = User.objects.get(username='tester')
        order = Order.objects.create(user=user, stall=stall, stall_name=stall.name, mode='simulation',
            status='cancelled', payment_status='refunded', payment_method='wechat',
            total_cents=product.price_cents, expires_at=now, paid_at=now,
            pickup_address='隔离备份恢复演练', pickup_latitude=31, pickup_longitude=121,
            inventory_released=True, idempotency_key='runtime-backup-' + run_id,
            request_hash=hashlib.sha256(run_id.encode()).hexdigest())
        OrderItem.objects.create(order=order, product=product, name=product.name,
            unit_price_cents=product.price_cents, quantity=1,
            portions=[{'note': '合成记录：恢复后保留逐份要求'}])
        payment = PaymentAttempt.objects.create(order=order, merchant=stall.merchant, mode='simulation',
            channel='simulation', account_key='runtime-backup-' + run_id, mchid='synthetic-mch', appid='synthetic-app',
            out_trade_no='BACKUPPAY' + run_id, transaction_id='BACKUPTX' + run_id, status='paid',
            simulation_state='SUCCESS', simulation_settled_at=now,
            amount_cents=order.total_cents, expires_at=now, paid_at=now,
            last_checked_at=now, next_query_at=now + timedelta(seconds=20), consecutive_query_failures=2)
        original = PaymentRefund.objects.create(order=order, payment=payment, requested_by=stall.merchant.user,
            mode='simulation', simulation_state='CLOSED', out_refund_no='BACKUPCLOSED' + run_id,
            amount_cents=order.total_cents, status='closed', reason='合成历史：旧退款关闭',
            last_checked_at=now, next_query_at=now + timedelta(seconds=30), consecutive_query_failures=3,
            resolved_at=now)
        retry = PaymentRefund.objects.create(order=order, payment=payment, requested_by=stall.merchant.user,
            mode='simulation', simulation_state='SUCCESS', simulation_settled_at=now,
            out_refund_no='BACKUPRETRY' + run_id, refund_id='BACKUPREFUND' + run_id,
            amount_cents=order.total_cents, status='success', reason='合成历史：新退款成功',
            replaces=original, source='retry', completed_at=now, resolved_at=now,
            last_checked_at=now, next_query_at=now + timedelta(seconds=5),
            operation_key='runtime-backup-refund-' + run_id,
            operation_hash=hashlib.sha256(('refund-' + run_id).encode()).hexdigest())
        evidence(order, 'runtime_backup_fixture', 'simulation', '仅验证恢复保真；不代表真实支付验收',
            actor=stall.merchant.user, payment=payment, refund=retry,
            facts={'synthetic_only': True, 'closed_refund': str(original.pk), 'retry_refund': str(retry.pk),
                   'amount_cents': order.total_cents, 'mode': 'simulation'})
        payment_fields = {'out_trade_no': payment.out_trade_no, 'mchid': payment.mchid, 'appid': payment.appid,
            'transaction_id': payment.transaction_id, 'trade_state': 'SUCCESS',
            'amount': {'total': order.total_cents, 'currency': 'CNY'}, 'success_time': now.isoformat()}
        refund_fields = {'out_trade_no': payment.out_trade_no, 'mchid': payment.mchid,
            'transaction_id': payment.transaction_id, 'out_refund_no': retry.out_refund_no,
            'refund_id': retry.refund_id, 'refund_status': 'SUCCESS',
            'amount': {'total': order.total_cents, 'refund': order.total_cents}, 'success_time': now.isoformat()}
        for event, resource in [('TRANSACTION.SUCCESS', payment_fields), ('REFUND.SUCCESS', refund_fields)]:
            PaymentNotification.objects.create(account_key=payment.account_key,
                event_id='BACKUP-' + event + '-' + run_id, event_type=event,
                mchid=payment.mchid, appid=payment.appid, resource=resource,
                payload_hash=hashlib.sha256(json.dumps(resource, sort_keys=True).encode()).hexdigest(),
                status='done', attempts=2, processed_at=now)
        selections = [Order.objects.filter(pk=order.pk), OrderItem.objects.filter(order=order),
                      PaymentAttempt.objects.filter(order=order), PaymentRefund.objects.filter(order=order),
                      FinancialEvidence.objects.filter(order=order),
                      PaymentNotification.objects.filter(account_key=payment.account_key)]
        counts = {rows.model._meta.db_table: rows.count() for rows in selections}
        if list(counts.values()) != [1, 1, 1, 2, 1, 2]:
            raise RuntimeError('Synthetic money history is incomplete')
    return {'synthetic_only': True, 'order_id': str(order.pk), 'table_rows': counts,
            'refund_history': ['closed', 'success'], 'refund_replacement_link': True,
            'query_schedule_fields_populated': True, 'notifications_terminal': True,
            'real_payment_enabled': False}
