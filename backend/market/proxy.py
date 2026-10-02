import re
import urllib.error
import urllib.parse
import urllib.request
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from rest_framework.decorators import api_view
from .errors import BusinessError


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None


@api_view(['GET'])
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
    query = request.GET.copy()
    query['key'], query['jscode'] = settings.AMAP_KEY, settings.AMAP_SECURITY_CODE
    # Disallow query forwarding fields that can ask the upstream to fetch arbitrary URLs.
    for field in ('url', 'target', 'host', 'redirect', 'jscode[]', 'key[]'):
        if field in query: raise BusinessError('地图请求参数无效。', status=400)
    if 'callback' in query and not re.fullmatch(r'[A-Za-z_$][A-Za-z0-9_$.]{0,100}', query['callback']):
        raise BusinessError('地图回调格式无效。', status=400)
    host = 'webapi.amap.com' if upstream_path == 'v4/map/styles' else 'restapi.amap.com'
    url = f'https://{host}/{upstream_path}?{query.urlencode()}'
    try:
        opener = urllib.request.build_opener(NoRedirect())
        with opener.open(urllib.request.Request(url, headers={'User-Agent': 'YanhuoMap/1.0'}), timeout=5) as upstream:
            body = upstream.read(2*1024*1024+1)
            if len(body) > 2*1024*1024: raise ValueError('response too large')
            # Do not expose upstream responses containing our security secret.
            if settings.AMAP_SECURITY_CODE.encode() in body: raise ValueError('secret in upstream response')
            response = HttpResponse(body, content_type=upstream.headers.get('Content-Type', 'application/json'))
            response['Cache-Control'] = 'private, no-store'
            return response
    except (urllib.error.URLError, TimeoutError, ValueError):
        return JsonResponse({'detail': '地图服务暂不可用，请使用列表选择摊位。', 'code': 'map_upstream_error'}, status=502)
