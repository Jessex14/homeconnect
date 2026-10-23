from django.test import override_settings
from django.core import mail
from rest_framework.test import APITestCase
from rest_framework import status

from .models import User
from apps.audit.models import SecurityEvent


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class AuthenticationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user("owner@example.test", "Strong-pass-4321")
        profile = self.user.profile
        profile.display_name = "Owner"
        profile.save()

    def test_login_success_and_me(self):
        response = self.client.post("/api/v1/auth/login", {"email": "owner@example.test", "password": "Strong-pass-4321"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, status.HTTP_200_OK)
        self.assertTrue(SecurityEvent.objects.filter(event_type="auth.login_succeeded").exists())

    def test_login_failure_is_generic_and_audited(self):
        response = self.client.post("/api/v1/auth/login", {"email": "owner@example.test", "password": "wrong"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["detail"], "Invalid email or password.")
        self.assertTrue(SecurityEvent.objects.filter(event_type="auth.login_failed", outcome="denied").exists())

    def test_unauthorized_me(self):
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, status.HTTP_403_FORBIDDEN)

    def test_logout_invalidates_session(self):
        login_response = self.client.post("/api/v1/auth/login", {"email": "owner@example.test", "password": "Strong-pass-4321"}, format="json")
        self.assertEqual(login_response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, status.HTTP_200_OK)
        self.assertEqual(self.client.post("/api/v1/auth/logout").status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, status.HTTP_403_FORBIDDEN)

    def test_malformed_login_input_rejected(self):
        response = self.client.post("/api/v1/auth/login", {"email": "not-an-email", "password": []}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_recovery_response_does_not_enumerate_accounts(self):
        existing = self.client.post("/api/v1/auth/recovery/request", {"email": "owner@example.test"}, format="json")
        missing = self.client.post("/api/v1/auth/recovery/request", {"email": "missing@example.test"}, format="json")
        self.assertEqual(existing.status_code, missing.status_code)
        self.assertEqual(existing.data, missing.data)
        self.assertEqual(len(mail.outbox), 1)

    def test_login_requires_csrf_token(self):
        from rest_framework.test import APIClient
        client = APIClient(enforce_csrf_checks=True)
        response = client.post("/api/v1/auth/login", {"email": "owner@example.test", "password": "Strong-pass-4321"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_password_recovery_invalidates_existing_session(self):
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        self.client.post("/api/v1/auth/login", {"email": "owner@example.test", "password": "Strong-pass-4321"}, format="json")
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        response = self.client.post("/api/v1/auth/recovery/confirm", {
            "uid": urlsafe_base64_encode(force_bytes(self.user.pk)),
            "token": default_token_generator.make_token(self.user),
            "new_password1": "Updated-pass-9876",
            "new_password2": "Updated-pass-9876",
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.client.get("/api/v1/auth/me").status_code, status.HTTP_403_FORBIDDEN)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Updated-pass-9876"))
