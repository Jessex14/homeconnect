from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include("apps.accounts.urls")),
    path("api/v1/", include("apps.organizations.urls")),
    path("api/v1/", include("apps.properties.urls")),
    path("api/v1/", include("apps.tenancies.urls")),
    path("api/v1/", include("apps.finance.urls")),
    path("api/v1/", include("apps.assistant.urls")),
    path("api/v1/", include("apps.maintenance.urls")),
    path("health/", include("apps.health.urls")),
    path("", include("apps.pwa.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
