from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from threading import Barrier
from unittest import skipUnless

from django.db import close_old_connections, connection
from django.core import mail
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from apps.accounts.models import User
from apps.organizations.models import Organization, OrganizationMembership, Property
from apps.properties.models import Unit
from .models import TenantInvitation, Tenancy
from .services import token_digest


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="homeconnect@example.test",
    TENANT_INVITATION_FRONTEND_URL="https://homeconnect.example.test/tenant-invitation",
)
class TenantInvitationFlowTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.owner = User.objects.create_user("invite-owner@example.test", "Strong-pass-4321")
        self.tenant = User.objects.create_user("invite-tenant@example.test", "Strong-pass-4321")
        self.other_user = User.objects.create_user("other-person@example.test", "Strong-pass-4321")
        self.organization = Organization.objects.create(name="Invitation portfolio")
        OrganizationMembership.objects.create(user=self.owner, organization=self.organization, role="owner")
        self.property = Property.objects.create(organization=self.organization, name="Garden House")
        self.unit = Unit.objects.create(property=self.property, code="G-2")

    def create_invitation(self):
        mail.outbox.clear()
        client = APIClient()
        client.force_authenticate(self.owner)
        response = client.post(
            f"/api/v1/units/{self.unit.pk}/invitations",
            {
                "email": self.tenant.email,
                "start_date": (timezone.localdate() + timedelta(days=10)).isoformat(),
                "agreed_rent": "25000.00",
                "due_day": 5,
                "deposit_amount": "10000.00",
                "deposit_terms": "Refundable subject to the signed agreement.",
                "agreement_reference": "AG-2026-02",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(len(mail.outbox), 1)
        return response.data

    def test_invitation_email_link_uses_fragment_and_acceptance_records_versioned_terms(self):
        invitation_data = self.create_invitation()
        message = mail.outbox[0].body
        self.assertIn("https://homeconnect.example.test/tenant-invitation#invitation_id=", message)
        link = next(line for line in message.splitlines() if "#invitation_id=" in line)
        self.assertNotIn("?token=", link)
        token = link.split("token=", 1)[1]
        invitation = TenantInvitation.objects.get(pk=invitation_data["id"])
        self.assertEqual(invitation.token_hash, token_digest(token))

        client = APIClient()
        client.force_authenticate(self.tenant)
        review = client.post("/api/v1/invitations/review", {"token": token}, format="json")
        self.assertEqual(review.status_code, status.HTTP_200_OK, review.data)
        self.assertEqual(review.data["property"]["name"], self.property.name)
        self.assertEqual(str(review.data["terms"]["agreed_rent"]), "25000.00")
        self.assertTrue(review.data["terms"]["end_date_is_exclusive"])

        accepted = client.post("/api/v1/invitations/accept", {"token": token}, format="json")
        self.assertEqual(accepted.status_code, status.HTTP_200_OK, accepted.data)
        tenancy = Tenancy.objects.get(pk=invitation.tenancy_id)
        self.assertEqual(tenancy.tenant_id, self.tenant.pk)
        self.assertEqual(tenancy.status, Tenancy.Status.ACCEPTED_PENDING_START)
        self.assertEqual(tenancy.accepted_terms_version, tenancy.terms_version)
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, TenantInvitation.Status.ACCEPTED)
        self.assertNotEqual(invitation.token_hash, token_digest(token))

    def test_invitation_token_cannot_be_reviewed_or_accepted_by_different_email(self):
        invitation_data = self.create_invitation()
        link = next(line for line in mail.outbox[0].body.splitlines() if "#invitation_id=" in line)
        token = link.split("token=", 1)[1]
        client = APIClient()
        client.force_authenticate(self.other_user)
        self.assertEqual(client.post("/api/v1/invitations/review", {"token": token}, format="json").status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(client.post("/api/v1/invitations/accept", {"token": token}, format="json").status_code, status.HTTP_404_NOT_FOUND)
        invitation = TenantInvitation.objects.get(pk=invitation_data["id"])
        self.assertEqual(invitation.status, TenantInvitation.Status.PENDING)

    def test_owner_can_list_resend_and_cancel_pending_unit_invitation(self):
        invitation_data = self.create_invitation()
        endpoint = f"/api/v1/units/{self.unit.pk}/invitations"
        client = APIClient()
        client.force_authenticate(self.owner)
        listed = client.get(endpoint)
        self.assertEqual(listed.status_code, status.HTTP_200_OK)
        self.assertEqual(listed.data[0]["id"], invitation_data["id"])
        self.assertEqual(listed.data[0]["delivery_status"], TenantInvitation.DeliveryStatus.SENT)

        old_link = next(line for line in mail.outbox[0].body.splitlines() if "#invitation_id=" in line)
        old_token = old_link.split("token=", 1)[1]
        resent = client.post(f"/api/v1/invitations/{invitation_data['id']}/resend", format="json")
        self.assertEqual(resent.status_code, status.HTTP_200_OK, resent.data)
        new_link = next(line for line in mail.outbox[-1].body.splitlines() if "#invitation_id=" in line)
        new_token = new_link.split("token=", 1)[1]
        self.assertNotEqual(old_token, new_token)

        tenant_client = APIClient()
        tenant_client.force_authenticate(self.tenant)
        self.assertEqual(tenant_client.post("/api/v1/invitations/review", {"token": old_token}, format="json").status_code, status.HTTP_404_NOT_FOUND)
        cancelled = client.post(f"/api/v1/invitations/{invitation_data['id']}/cancel", format="json")
        self.assertEqual(cancelled.status_code, status.HTTP_200_OK)
        self.assertEqual(client.get(endpoint).data[0]["status"], TenantInvitation.Status.CANCELLED)

    def test_unrelated_user_cannot_list_unit_invitations(self):
        self.create_invitation()
        client = APIClient()
        client.force_authenticate(self.other_user)
        self.assertEqual(client.get(f"/api/v1/units/{self.unit.pk}/invitations").status_code, status.HTTP_404_NOT_FOUND)


@skipUnless(connection.vendor == "postgresql", "Row-lock concurrency acceptance requires PostgreSQL")
@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ConcurrentTenancyAcceptanceTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.owner = User.objects.create_user("concurrency-owner@example.test", "Strong-pass-4321")
        self.organization = Organization.objects.create(name="Concurrent portfolio")
        OrganizationMembership.objects.create(user=self.owner, organization=self.organization, role="owner")
        self.property = Property.objects.create(organization=self.organization, name="Concurrent property")
        self.unit = Unit.objects.create(property=self.property, code="C-1")

    def test_concurrent_overlapping_invitations_only_one_succeeds(self):
        barrier = Barrier(2)
        start = (date.today() + timedelta(days=4)).isoformat()

        def create_invitation(email):
            close_old_connections()
            client = APIClient()
            client.force_authenticate(self.owner)
            barrier.wait(timeout=10)
            response = client.post(
                f"/api/v1/units/{self.unit.pk}/invitations",
                {
                    "email": email,
                    "start_date": start,
                    "agreed_rent": "10000.00",
                    "due_day": 5,
                    "deposit_amount": "0.00",
                },
                format="json",
            )
            close_old_connections()
            return response.status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(create_invitation, ["one@example.test", "two@example.test"]))
        self.assertCountEqual(statuses, [201, 409])
