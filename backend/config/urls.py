import posixpath
from django.contrib import admin
from django.conf import settings
from django.http import HttpResponseNotFound
from django.urls import include, path, re_path
from django.views.static import serve as static_serve
from market.auth_limits import LimitedAdminAuthenticationForm
from market.merchant import protected_payment_qr


def serve_public_media(request, path=''):
    raw = (path or '').replace('\\', '/')
    segments = [part for part in raw.split('/') if part]
    normalized = posixpath.normpath('/' + raw).lstrip('/')
    if (not normalized or normalized == '.'
            or any(part in ('.', '..') or part.lower().startswith('qr-') for part in raw.split('/') if part)
            or any(part.lower().startswith('qr-') for part in segments)):
        return HttpResponseNotFound(headers={'Cache-Control': 'private, no-store'})
    return static_serve(request, normalized, document_root=settings.MEDIA_ROOT)


admin.site.login_form = LimitedAdminAuthenticationForm
urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/v1/', include('market.urls')),
    re_path(r'^media/merchants/(?P<stall_id>[0-9]+)/(?P<filename>qr-[A-Za-z0-9._-]+)$', protected_payment_qr),
]
if settings.DEBUG:
    media_prefix = settings.MEDIA_URL.lstrip('/')
    urlpatterns.append(re_path(rf'^{media_prefix}(?P<path>.*)$', serve_public_media))
admin.site.site_header = '烟火地图 · 运营后台'
admin.site.site_title = '烟火地图'
