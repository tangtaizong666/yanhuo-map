"""Explicit inbox delivery for existing financial state-machine test fixtures."""
from .notification_inbox import process_notifications
from .payments import handle_notification as enqueue_notification
from .wechatpay import GatewayError


def deliver_notification(account_key, headers, body):
    row = enqueue_notification(account_key, headers, body)
    process_notifications()
    row.refresh_from_db()
    if row.status != 'done':
        raise GatewayError(row.last_error_code or 'NOTIFICATION_NOT_DELIVERED', '测试通知未完成。')
    return row
