from django.test import TestCase, RequestFactory, override_settings

from apps.accounts.models import User
from .models import SecurityEvent
from .services import record_security_event


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class SecurityAuditTests(TestCase):
    def test_event_is_written_without_forwarded_ip_trust(self):
        request = RequestFactory().get("/", HTTP_X_FORWARDED_FOR="203.0.113.2", REMOTE_ADDR="10.0.0.1", HTTP_X_REQUEST_ID="req-1")
        event = record_security_event("test.event", request)
        self.assertEqual(event.ip_address, "10.0.0.1")
        self.assertEqual(event.request_id, "req-1")

    def test_events_are_append_only_via_model_methods(self):
        user = User.objects.create_user("audit@example.test", "Strong-pass-4321")
        event = SecurityEvent.objects.create(actor=user, event_type="test.event")
        event.event_type = "changed"
        with self.assertRaises(ValueError):
            event.save()
        with self.assertRaises(ValueError):
            event.delete()
        with self.assertRaises(ValueError):
            SecurityEvent.objects.filter(pk=event.pk).update(event_type="changed")
        with self.assertRaises(ValueError):
            SecurityEvent.objects.filter(pk=event.pk).delete()

    def test_cross_tenant_profile_is_not_exposed_by_me_endpoint(self):
        from rest_framework.test import APIClient
        owner = User.objects.create_user("one@example.test", "Strong-pass-4321")
        other = User.objects.create_user("two@example.test", "Strong-pass-4321")
        client = APIClient()
        client.force_authenticate(owner)
        response = client.get("/api/v1/auth/me")
        self.assertEqual(response.data["email"], owner.email)
        self.assertNotEqual(response.data["email"], other.email)
