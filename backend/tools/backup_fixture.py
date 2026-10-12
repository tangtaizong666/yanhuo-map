"""Synthetic money history for the disposable restore drill, never a real ledger."""
import hashlib
import json
from datetime import timedelta
from django.conf import settings
from django.contrib.auth.models import User
from django.db import connection
from django.utils import timezone


def seed_restore_fixture():
    if settings.PRODUCTION or connection.vendor != 'postgresql' or connection.settings_dict['NAME'] != 'yanhuo_operations_check':
        raise RuntimeError('Restore fixtures require the dedicated disposable verification database')
    from market.financial import evidence
    from market.models import Order, OrderItem, PaymentAttempt, PaymentRefund, PaymentNotification, Stall
    stall = Stall.objects.filter(is_demo=True).first()
    product = stall.products.first()
    user = User.objects.get(username='student')
    order = Order.objects.create(user=user, stall=stall, stall_name=stall.name, mode='simulation',
        status='cancelled', payment_status='refunded', payment_method='wechat',
        total_cents=product.price_cents, expires_at=timezone.now(), paid_at=timezone.now(),
        pickup_address='隔离恢复演练', pickup_latitude=31, pickup_longitude=121,
        idempotency_key='isolated-restore-fixture', request_hash='fixture')
    OrderItem.objects.create(order=order, product=product, name=product.name,
        unit_price_cents=product.price_cents, quantity=1, portions=[{'note': '恢复时保留逐份要求'}])
    payment = PaymentAttempt.objects.create(order=order, merchant=stall.merchant, mode='simulation',
        channel='simulation', account_key='fixture', mchid='fixture', appid='fixture',
        out_trade_no='RESTORETESTPAYMENT', transaction_id='RESTORETESTTRANSACTION', status='paid',
        amount_cents=order.total_cents, expires_at=timezone.now(), paid_at=timezone.now(),
        next_query_at=timezone.now()+timedelta(seconds=20), consecutive_query_failures=2)
    original = PaymentRefund.objects.create(order=order, payment=payment, mode='simulation',
        out_refund_no='RESTORETESTCLOSED', amount_cents=order.total_cents, status='closed', reason='演练旧尝试',
        resolved_at=timezone.now())
    retry = PaymentRefund.objects.create(order=order, payment=payment, mode='simulation',
        out_refund_no='RESTORETESTRETRY', refund_id='RESTORETESTREFUND', amount_cents=order.total_cents,
        status='success', reason='演练新尝试', replaces=original, source='retry',
        completed_at=timezone.now(), resolved_at=timezone.now(), operation_key='restore-retry-key')
    evidence(order, 'restore_fixture', 'simulation', '仅验证备份保真，不代表真实支付',
        actor=user, payment=payment, refund=retry, facts={'fixture': True})
    facts = {'out_trade_no': payment.out_trade_no, 'mchid': payment.mchid, 'appid': payment.appid,
        'transaction_id': payment.transaction_id, 'trade_state': 'SUCCESS',
        'amount': {'total': order.total_cents, 'currency': 'CNY'}, 'success_time': payment.paid_at.isoformat()}
    PaymentNotification.objects.create(account_key='restore-fixture-only', event_id='RESTORETESTEVENT',
        event_type='TRANSACTION.SUCCESS', mchid=payment.mchid, appid=payment.appid, resource=facts,
        payload_hash=hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest(),
        status='retry', attempts=2, last_error_code='RESTORE_FIXTURE_ONLY',
        available_at=timezone.now()+timedelta(minutes=10))
    print('Created simulated payment, closed/successful refunds, evidence, query pacing and a synthetic inbox record for restore verification.')
