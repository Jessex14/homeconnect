from django.urls import path

from .views import app_shell, service_worker


urlpatterns = [
    path("", app_shell, name="app-shell"),
    path("listings", app_shell, name="public-rentals-shell"),
    path("tenant-invitation", app_shell, name="tenant-invitation-shell"),
    path("service-worker.js", service_worker, name="service-worker"),
]
