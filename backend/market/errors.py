import logging
from django.core.exceptions import RequestDataTooBig
from rest_framework.exceptions import APIException, NotAuthenticated
from rest_framework.views import exception_handler as default_handler
from rest_framework.response import Response

logger = logging.getLogger('market')

class BusinessError(APIException):
    status_code = 409
    default_detail = '当前状态无法完成此操作。'
    default_code = 'conflict'
    def __init__(self, detail, code='conflict', status=409, **extra):
        self.status_code = status
        self.detail = {'detail': detail, 'code': code, **extra}

def exception_handler(exc, context):
    if isinstance(exc, RequestDataTooBig):
        return Response({'detail': '请求内容过大，请减少提交内容。', 'code': 'request_too_large'}, status=413)
    response = default_handler(exc, context)
    if response is not None:
        if isinstance(exc, BusinessError) and exc.status_code == 429 and exc.detail.get('retry_after'):
            response['Retry-After'] = str(exc.detail['retry_after'])
        if not isinstance(response.data, dict) or 'detail' not in response.data:
            response.data = {'detail': '请检查填写内容。', 'errors': response.data}
        if isinstance(exc, NotAuthenticated): response.data['code'] = 'not_authenticated'
        return response
    logger.exception('api_failure path=%s', context.get('request').path if context.get('request') else '?')
    return Response({'detail': '服务暂时不可用，请稍后重试。', 'code': 'server_error'}, status=500)
