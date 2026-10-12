import logging
from threading import BoundedSemaphore

from django.conf import settings
from django.db import DatabaseError
from rest_framework import serializers
from rest_framework.decorators import api_view, authentication_classes, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .errors import BusinessError
from . import payments
from .serializers import OrderSerializer
from .views import csrf, order_query
from .wechatpay import ConfigurationError, GatewayError


_notification_slots = BoundedSemaphore(2)


def _response(order, request, *, merchant=False):
    return Response(OrderSerializer(order_query().get(pk=order.pk), context={'merchant': merchant, 'request': request}).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def start(request, order_id):
    csrf(request)
    channel = serializers.ChoiceField(choices=['native', 'h5', 'simulation']).run_validation(request.data.get('channel'))
    from .auth_limits import client_ip
    peer = client_ip(request, trust_proxy=settings.WECHAT_PAY_TRUST_PROXY_CLIENT_IP)
    return _response(payments.start_payment(order_id, request.user, channel, peer), request)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def sync(request, order_id):
    csrf(request)
    return _response(payments.sync_payment(order_id, request.user), request)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def close(request, order_id):
    csrf(request)
    return _response(payments.close_payment(order_id, request.user), request)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def refund(request, order_id):
    csrf(request)
    reason = serializers.CharField(max_length=80, allow_blank=False, trim_whitespace=True).run_validation(request.data.get('reason'))
    if len(reason.encode('utf-8')) > 80:
        raise BusinessError('退款原因最多约 26 个汉字，请简要填写。', 'invalid_reason', status=400)
    outcome = serializers.ChoiceField(choices=['success', 'failure', 'pending']).run_validation(request.data['simulation_outcome']) if 'simulation_outcome' in request.data else None
    return _response(payments.request_refund(order_id, request.user, reason, outcome), request, merchant=True)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def simulate(request, order_id):
    from .simulation import simulate_payment
    csrf(request)
    payment_id = serializers.UUIDField().run_validation(request.data.get('payment_id'))
    outcome = serializers.ChoiceField(choices=['success', 'failure', 'pending']).run_validation(request.data.get('outcome'))
    return _response(simulate_payment(order_id, request.user, payment_id, outcome), request)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def simulate_refund(request, order_id):
    from .simulation import simulate_refund as apply
    csrf(request)
    refund_id = serializers.UUIDField().run_validation(request.data.get('refund_id'))
    outcome = serializers.ChoiceField(choices=['success', 'failure', 'pending']).run_validation(request.data.get('outcome'))
    return _response(apply(order_id, request.user, refund_id, outcome), request, merchant=True)


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([])
def notify(request, account_key):
    # This is the sole unauthenticated payment endpoint; verified WeChat signatures
    # replace session CSRF. Use exact raw bytes, never DRF's reserialized request.data.
    from .notification_inbox import MAX_NOTIFICATION_BYTES
    try:
        length = int(request.META.get('CONTENT_LENGTH') or 0)
    except (TypeError, ValueError):
        return Response({'code': 'FAIL', 'message': '通知格式无效。'}, status=400)
    if length > MAX_NOTIFICATION_BYTES:
        return Response({'code': 'FAIL', 'message': '通知过大。'}, status=413)
    if not _notification_slots.acquire(blocking=False):
        return Response({'code': 'FAIL', 'message': '通知接收繁忙，请重试。'}, status=503, headers={'Retry-After': '1'})
    try:
        body = request.body
        if len(body) > MAX_NOTIFICATION_BYTES:
            return Response({'code': 'FAIL', 'message': '通知过大。'}, status=413)
        payments.handle_notification(account_key, request.headers, body)
    except GatewayError as exc:
        status = 503 if exc.retryable or isinstance(exc, ConfigurationError) else 400
        return Response({'code': 'FAIL', 'message': '通知未通过校验或暂时无法处理，请重试。'}, status=status)
    except DatabaseError as exc:
        logging.getLogger('market').warning('payment_notification_ingest_failed type=%s', type(exc).__name__)
        return Response({'code': 'FAIL', 'message': '通知暂时无法保存，请重试。'}, status=503, headers={'Retry-After': '1'})
    finally:
        _notification_slots.release()
    return Response(status=204)
