"""Bound JSON before decoding even on direct development/test WSGI requests."""
import io

from django.conf import settings
from rest_framework.exceptions import APIException
from rest_framework.parsers import FormParser, JSONParser


class PayloadTooLarge(APIException):
    status_code = 413
    default_code = 'request_too_large'
    default_detail = {'detail': '请求内容过大，请减少提交内容。', 'code': 'request_too_large'}


class BoundedBodyMixin:
    def parse(self, stream, media_type=None, parser_context=None):
        limit = settings.DATA_UPLOAD_MAX_MEMORY_SIZE
        payload = stream.read(limit + 1)
        if len(payload) > limit:
            raise PayloadTooLarge()
        return super().parse(io.BytesIO(payload), media_type, parser_context)


class BoundedJSONParser(BoundedBodyMixin, JSONParser):
    pass


class BoundedFormParser(BoundedBodyMixin, FormParser):
    pass
