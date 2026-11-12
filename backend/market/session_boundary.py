"""Reject stale-tab writes; this optional assertion never grants authentication."""
from django.http import JsonResponse


class SessionActorMiddleware:
    # These explicitly change identity, or authenticate with provider signatures.
    IDENTITY_ACTIONS = {'/api/v1/auth/login', '/api/v1/auth/register', '/api/v1/auth/recovery/reset'}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (request.method not in ('GET', 'HEAD', 'OPTIONS', 'TRACE')
                and request.path.startswith('/api/v1/')
                and request.path not in self.IDENTITY_ACTIONS
                and not request.path.startswith('/api/v1/payments/wechat/notify/')):
            expected = request.headers.get('X-Yanhuo-Actor')
            # Existing clients remain compatible. The SPA always sends the
            # identity whose state produced the action, including anonymous.
            if expected is not None:
                actual = str(request.user.pk) if request.user.is_authenticated else 'anonymous'
                if expected != actual:
                    return JsonResponse({
                        'detail': '账号已变化，本次操作未提交，请在当前账号重新确认。',
                        'code': 'session_changed', 'submitted': False,
                    }, status=409)
        return self.get_response(request)
