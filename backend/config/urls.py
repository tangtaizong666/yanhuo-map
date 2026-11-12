from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path, re_path
from market.auth_limits import LimitedAdminAuthenticationForm
from market.merchant import protected_payment_qr

admin.site.login_form = LimitedAdminAuthenticationForm
urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/v1/', include('market.urls')),
    re_path(r'^media/merchants/(?P<stall_id>[0-9]+)/(?P<filename>qr-[A-Za-z0-9._-]+)$', protected_payment_qr),
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
admin.site.site_header = '烟火地图 · 运营后台'
admin.site.site_title = '烟火地图'
