from django.urls import path

from .views import LoginView, LogoutView, MeView, RecoveryConfirmView, RecoveryRequestView

urlpatterns = [
    path("auth/login", LoginView.as_view(), name="login"),
    path("auth/logout", LogoutView.as_view(), name="logout"),
    path("auth/me", MeView.as_view(), name="me"),
    path("auth/recovery/request", RecoveryRequestView.as_view(), name="recovery-request"),
    path("auth/recovery/confirm", RecoveryConfirmView.as_view(), name="recovery-confirm"),
]
