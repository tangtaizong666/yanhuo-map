from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path

urlpatterns = [path('admin/', admin.site.urls), path('api/v1/', include('market.urls'))]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
admin.site.site_header = '烟火地图 · 运营后台'
admin.site.site_title = '烟火地图'
