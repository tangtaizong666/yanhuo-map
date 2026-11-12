from datetime import timedelta
import re
from threading import BoundedSemaphore
import urllib.error
import urllib.parse
import urllib.request
from django.conf import settings
from django.db.models import F
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.throttling import SimpleRateThrottle
from .auth_limits import client_ip_bucket
from .errors import BusinessError
from .security_models import DailyQuotaCounter

_proxy_slots = BoundedSemaphore(4)
FORBIDDEN_QUERY_KEYS = frozenset({'url', 'target', 'host', 'redirect', 'jscode[]', 'key[]'})


class AmapProxyThrottle(SimpleRateThrottle):
    # Origin/Referer are forgeable, so a per-client limit is the real guard on the key's quota.
    scope = 'amap_proxy'
    rate = '60/minute'
    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': client_ip_bucket(request)}


def daily_quota_exhausted():
    # Database-atomic daily ceiling keeps concurrent workers from racing past the AMap console quota.
    limit = settings.AMAP_PROXY_DAILY_LIMIT
    if limit <= 0:
        return True
    today = timezone.localdate()
    now = timezone.now()
    updated = DailyQuotaCounter.objects.filter(
        scope='amap_proxy', day=today, count__lt=limit
    ).update(count=F('count') + 1, updated_at=now)
    if updated:
        return False
    if DailyQuotaCounter.objects.filter(scope='amap_proxy', day=today).exists():
        return True
    _, created = DailyQuotaCounter.objects.get_or_create(
        scope='amap_proxy', day=today, defaults={'count': 0, 'updated_at': now}
    )
    if created:
        DailyQuotaCounter.objects.filter(day__lt=today - timedelta(days=90)).delete()
    updated = DailyQuotaCounter.objects.filter(
        scope='amap_proxy', day=today, count__lt=limit
    ).update(count=F('count') + 1, updated_at=now)
    return updated == 0


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None


@api_view(['GET'])
@permission_classes([AllowAny])
@throttle_classes([AmapProxyThrottle])
def amap_proxy(request, upstream_path=''):
    if not settings.AMAP_KEY or not settings.AMAP_SECURITY_CODE:
        return JsonResponse({'detail': '地图服务尚未配置。', 'code': 'map_unavailable'}, status=503)
    # Never proxy a caller-provided host or redirect. Only a fixed, limited API surface.
    if not re.fullmatch(r'(?:v3|v4|v5)/[A-Za-z0-9/_-]+', upstream_path) or '..' in upstream_path:
        raise BusinessError('地图请求路径无效。', 'invalid_map_path', status=400)
    allowed_prefixes = ('v3/ip', 'v3/geocode/', 'v3/direction/', 'v3/assistant/coordinate/', 'v3/place/', 'v3/config/',
                        'v4/map/styles', 'v4/ip', 'v4/direction/', 'v5/direction/', 'v5/place/')
    if not upstream_path.startswith(allowed_prefixes): raise BusinessError('地图接口未开放。', status=400)
    source = request.headers.get('Origin') or request.headers.get('Referer')
    allowed_hosts = {request.get_host(), *[urllib.parse.urlparse(o).netloc for o in settings.CSRF_TRUSTED_ORIGINS]}
    if not source or urllib.parse.urlparse(source).netloc not in allowed_hosts:
        raise BusinessError('地图请求来源无效。', 'invalid_origin', status=403)
    # Reject duplicate query parameters and case-folded forwarding fields before touching quota.
    for raw_key, values in request.GET.lists():
        if len(values) > 1 or raw_key.lower() in FORBIDDEN_QUERY_KEYS:
            raise BusinessError('地图请求参数无效。', status=400)
    if 'callback' in request.GET and not re.fullmatch(r'[A-Za-z_$][A-Za-z0-9_$.]{0,100}', request.GET['callback']):
        raise BusinessError('地图回调格式无效。', status=400)
    if daily_quota_exhausted():
        return JsonResponse({'detail': '今日地图服务额度已用完，请先用列表找摊。', 'code': 'map_quota_exhausted'}, status=429)
    if not _proxy_slots.acquire(blocking=False):
        return JsonResponse({'detail': '地图服务繁忙，请稍后再试或使用列表找摊。', 'code': 'map_busy'}, status=503)
    query = request.GET.copy()
    for existing_key in [k for k in query if k.lower() in ('key', 'jscode')]:
        query.pop(existing_key, None)
    query['key'], query['jscode'] = settings.AMAP_KEY, settings.AMAP_SECURITY_CODE
    host = 'webapi.amap.com' if upstream_path == 'v4/map/styles' else 'restapi.amap.com'
    url = f'https://{host}/{upstream_path}?{query.urlencode()}'
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(urllib.request.Request(url, headers={'User-Agent': 'YanhuoMap/1.0'}), timeout=5) as upstream:
            body = upstream.read(2*1024*1024+1)
            if len(body) > 2*1024*1024: raise ValueError('response too large')
            # Do not expose upstream responses containing our security secret (case-insensitive).
            if settings.AMAP_SECURITY_CODE.lower().encode() in body.lower():
                raise ValueError('secret in upstream response')
            upstream_ct = (upstream.headers.get('Content-Type') or '').split(';', 1)[0].strip().lower()
            if 'callback' in query:
                content_type = 'application/javascript; charset=utf-8'
            elif upstream_path == 'v4/map/styles' and upstream_ct == 'application/octet-stream':
                content_type = 'application/octet-stream'
            else:
                content_type = 'application/json; charset=utf-8'
            response = HttpResponse(body, content_type=content_type)
            response['Cache-Control'] = 'private, no-store'
            response['X-Content-Type-Options'] = 'nosniff'
            response['Content-Security-Policy'] = "default-src 'none'; sandbox"
            return response
    except (urllib.error.URLError, TimeoutError, ValueError):
        return JsonResponse({'detail': '地图服务暂不可用，请使用列表选择摊位。', 'code': 'map_upstream_error'}, status=502)
    finally:
        _proxy_slots.release()
