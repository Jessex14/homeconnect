import logging

from django.conf import settings
from django.contrib.auth import login, logout
from django.contrib.auth.forms import PasswordResetForm, SetPasswordForm
from django.contrib.auth.tokens import default_token_generator
from django.utils.decorators import method_decorator
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.views.decorators.csrf import csrf_protect
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.services import record_security_event

from .models import User
from .serializers import LoginSerializer, UserSerializer

logger = logging.getLogger(__name__)


@method_decorator(csrf_protect, name="dispatch")
class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        if not serializer.is_valid():
            record_security_event("auth.login_failed", request, outcome="denied")
            return Response({"detail": "Invalid email or password."}, status=status.HTTP_400_BAD_REQUEST)
        user = serializer.validated_data["user"]
        login(request, user)
        record_security_event("auth.login_succeeded", request, actor=user)
        return Response(UserSerializer(user).data)


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        actor = request.user
        logout(request)
        record_security_event("auth.logout", request, actor=actor)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)


class RecoveryRequestView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        form = PasswordResetForm(data={"email": request.data.get("email", "")})
        if form.is_valid():
            form.save(
                request=request,
                use_https=request.is_secure(),
                email_template_name="accounts/password_reset_email.html",
                subject_template_name="accounts/password_reset_subject.txt",
                extra_email_context={"frontend_url": settings.PASSWORD_RESET_FRONTEND_URL},
            )
        record_security_event("auth.recovery_requested", request, outcome="accepted")
        return Response({"detail": "If the account exists, recovery instructions will be sent."}, status=202)


class RecoveryConfirmView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        try:
            user = User.objects.get(pk=force_str(urlsafe_base64_decode(request.data.get("uid", ""))))
        except (User.DoesNotExist, ValueError, TypeError, OverflowError):
            user = None
        token = request.data.get("token", "")
        if user is None or not default_token_generator.check_token(user, token):
            record_security_event("auth.recovery_failed", request, outcome="denied")
            return Response({"detail": "Invalid or expired recovery link."}, status=400)
        form = SetPasswordForm(user, {"new_password1": request.data.get("new_password1"), "new_password2": request.data.get("new_password2")})
        if not form.is_valid():
            return Response({"detail": form.errors}, status=400)
        form.save()
        # Django's password change invalidates existing sessions at next request.
        from django.contrib.sessions.models import Session
        from django.utils import timezone
        for session in Session.objects.filter(expire_date__gt=timezone.now()):
            data = session.get_decoded()
            if data.get("_auth_user_id") == str(user.pk):
                session.delete()
        record_security_event("auth.recovery_succeeded", request, actor=user)
        return Response({"detail": "Password updated. Please sign in again."})
