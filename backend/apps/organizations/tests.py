from rest_framework.test import APITestCase
from rest_framework import status
from django.test import override_settings
from datetime import timedelta
from urllib.parse import parse_qs, urlparse
from django.core import mail
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import DomainAuditEvent
from .models import Organization, OrganizationMembership, PropertyScope, PropertyAssignment


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ScopedAuthorizationTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner@example.test", "Strong-pass-4321")
        self.other_owner = User.objects.create_user("other@example.test", "Strong-pass-4321")
        self.tenant = User.objects.create_user("tenant@example.test", "Strong-pass-4321")
        self.org = Organization.objects.create(name="Owner portfolio")
        self.other_org = Organization.objects.create(name="Other portfolio")
        OrganizationMembership.objects.create(user=self.owner, organization=self.org, role="owner")
        OrganizationMembership.objects.create(user=self.other_owner, organization=self.other_org, role="owner")
        OrganizationMembership.objects.create(user=self.tenant, organization=self.org, role="tenant")
        self.prop = PropertyScope.objects.create(organization=self.org, name="Home")
        self.other_prop = PropertyScope.objects.create(organization=self.other_org, name="Private")

    def test_owner_sees_only_owned_organization_properties(self):
        self.client.force_authenticate(self.owner)
        response = self.client.get("/api/v1/property-scopes")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([item["id"] for item in response.data], [str(self.prop.id)])

    def test_tenant_does_not_see_other_organization(self):
        self.client.force_authenticate(self.tenant)
        self.assertEqual(self.client.get("/api/v1/property-scopes").data, [])
        self.assertEqual(self.client.get(f"/api/v1/property-scopes/{self.other_prop.id}").status_code, status.HTTP_404_NOT_FOUND)

    def test_cross_landlord_data_access_denied(self):
        self.client.force_authenticate(self.other_owner)
        self.assertEqual(self.client.get(f"/api/v1/property-scopes/{self.prop.id}").status_code, status.HTTP_404_NOT_FOUND)

    def test_manager_requires_property_assignment(self):
        manager = User.objects.create_user("manager@example.test", "Strong-pass-4321")
        membership = OrganizationMembership.objects.create(user=manager, organization=self.org, role="manager")
        self.client.force_authenticate(manager)
        self.assertEqual(self.client.get("/api/v1/property-scopes").data, [])
        PropertyAssignment.objects.create(property_scope=self.prop, membership=membership, role="manager")
        self.assertEqual(len(self.client.get("/api/v1/property-scopes").data), 1)

    def test_invalid_uuid_does_not_expose_records(self):
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.get("/api/v1/property-scopes/not-a-uuid").status_code, status.HTTP_404_NOT_FOUND)

    def test_user_can_create_organization_and_becomes_owner_atomically(self):
        new_user = User.objects.create_user("new-owner@example.test", "Strong-pass-4321")
        self.client.force_authenticate(new_user)
        response = self.client.post("/api/v1/organizations", {"name": "  New Portfolio  "}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["name"], "New Portfolio")
        self.assertTrue(OrganizationMembership.objects.filter(
            organization_id=response.data["id"], user=new_user, role=OrganizationMembership.Role.OWNER, is_active=True
        ).exists())

    def test_organization_access_is_membership_scoped(self):
        self.client.force_authenticate(self.other_owner)
        self.assertEqual(self.client.get(f"/api/v1/organizations/{self.org.id}").status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.get("/api/v1/organizations").data[0]["id"], str(self.other_org.id))


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class PropertyAndTenancyAcceptanceTests(APITestCase):
    """Phase criteria: scoped CRUD, self-service invitation acceptance, valid date/overlap rules, and retained history."""

    def setUp(self):
        self.owner = User.objects.create_user("owner2@example.test", "Strong-pass-4321")
        self.tenant = User.objects.create_user("tenant2@example.test", "Strong-pass-4321")
        self.other_tenant = User.objects.create_user("other-tenant@example.test", "Strong-pass-4321")
        self.outsider = User.objects.create_user("outsider@example.test", "Strong-pass-4321")
        self.org = Organization.objects.create(name="Portfolio")
        self.other_org = Organization.objects.create(name="Outside portfolio")
        OrganizationMembership.objects.create(user=self.owner, organization=self.org, role="owner")
        OrganizationMembership.objects.create(user=self.tenant, organization=self.org, role="tenant")
        OrganizationMembership.objects.create(user=self.other_tenant, organization=self.org, role="tenant")
        OrganizationMembership.objects.create(user=self.outsider, organization=self.other_org, role="owner")
        self.client.force_authenticate(self.owner)

    def create_property_and_unit(self):
        property_response = self.client.post(
            f"/api/v1/organizations/{self.org.id}/properties",
            {"name": "Riverside", "address": "Nairobi"}, format="json",
        )
        self.assertEqual(property_response.status_code, status.HTTP_201_CREATED, property_response.data)
        self.property_id = property_response.data["id"]
        unit_response = self.client.post(
            f"/api/v1/properties/{self.property_id}/units",
            {"code": "A-1", "label": "One bedroom"}, format="json",
        )
        self.assertEqual(unit_response.status_code, status.HTTP_201_CREATED, unit_response.data)
        self.unit_id = unit_response.data["id"]

    def invite(self, email=None, start_date=None, end_date=None):
        from datetime import date
        return self.client.post(
            f"/api/v1/units/{self.unit_id}/invitations",
            {
                "email": email or self.tenant.email,
                # Never trust a caller-supplied tenant id; binding is based on the signed-in
                # invitee whose email matches the one-time invitation token.
                "tenant_id": self.other_tenant.pk,
                "start_date": (start_date or date.today() + timedelta(days=2)).isoformat(),
                "end_date": end_date.isoformat() if end_date else None,
                "agreed_rent": "25000.00",
                "due_day": 5,
                "deposit_amount": "25000.00",
                "deposit_terms": "Recorded as agreed deposit terms.",
                "agreement_reference": "AGR-001",
            }, format="json",
        )

    def invitation_token(self):
        body = mail.outbox[-1].body
        invite_link = next(line for line in body.splitlines() if line.startswith("http"))
        return parse_qs(urlparse(invite_link).fragment)["token"][0]

    def test_property_and_unit_create_update_and_cross_org_isolation(self):
        self.create_property_and_unit()
        response = self.client.patch(
            f"/api/v1/properties/{self.property_id}", {"name": "Riverside Updated"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], "Riverside Updated")
        self.assertTrue(DomainAuditEvent.objects.filter(action="property.updated", object_id=self.property_id).exists())
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(f"/api/v1/properties/{self.property_id}").status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.patch(f"/api/v1/units/{self.unit_id}", {"label": "Leaked"}, format="json").status_code, status.HTTP_404_NOT_FOUND)

    def test_invitation_requires_matching_authenticated_invitee_and_accepts_self(self):
        self.create_property_and_unit()
        response = self.invite()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        from apps.tenancies.models import TenantInvitation, Tenancy
        invitation = TenantInvitation.objects.get(pk=response.data["id"])
        raw_token = self.invitation_token()
        self.assertNotEqual(invitation.token_hash, raw_token)
        self.client.force_authenticate(self.other_tenant)
        denied = self.client.post("/api/v1/invitations/review", {"token": raw_token}, format="json")
        self.assertEqual(denied.status_code, status.HTTP_404_NOT_FOUND)
        self.client.force_authenticate(self.tenant)
        review = self.client.post("/api/v1/invitations/review", {"token": raw_token}, format="json")
        self.assertEqual(review.status_code, status.HTTP_200_OK, review.data)
        accepted = self.client.post("/api/v1/invitations/accept", {"token": raw_token}, format="json")
        self.assertEqual(accepted.status_code, status.HTTP_200_OK, accepted.data)
        replay = self.client.post("/api/v1/invitations/accept", {"token": raw_token}, format="json")
        self.assertEqual(replay.status_code, status.HTTP_404_NOT_FOUND)
        tenancy = Tenancy.objects.get(pk=accepted.data["id"])
        self.assertEqual(tenancy.tenant_id, self.tenant.id)
        self.assertEqual(tenancy.status, Tenancy.Status.ACCEPTED_PENDING_START)
        self.assertEqual(tenancy.terms_history.count(), 1)
        terms = tenancy.terms_history.get()
        with self.assertRaises(ValueError):
            terms.delete()
        with self.assertRaises(ValueError):
            tenancy.terms_history.update(agreed_rent="1.00")
        tenant_detail = self.client.get(f"/api/v1/tenancies/{tenancy.id}")
        self.assertEqual(tenant_detail.data["agreed_rent"], "25000.00")
        self.assertFalse(tenant_detail.data["can_manage"])
        self.assertFalse(tenant_detail.data["can_activate"])
        self.client.force_authenticate(self.owner)
        owner_detail = self.client.get(f"/api/v1/tenancies/{tenancy.id}")
        self.assertTrue(owner_detail.data["can_manage"])
        self.assertFalse(owner_detail.data["can_activate"])
        self.assertFalse(owner_detail.data["can_end"])
        self.client.force_authenticate(self.other_tenant)
        self.assertEqual(self.client.get(f"/api/v1/tenancies/{tenancy.id}").status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.get(f"/api/v1/units/{self.unit_id}").status_code, status.HTTP_404_NOT_FOUND)

    def test_duplicate_pending_invitation_returns_conflict(self):
        self.create_property_and_unit()
        self.assertEqual(self.invite().status_code, status.HTTP_201_CREATED)
        duplicate = self.invite()
        self.assertEqual(duplicate.status_code, status.HTTP_409_CONFLICT)

    def test_expired_invitation_cannot_be_reviewed_or_accepted(self):
        self.create_property_and_unit()
        self.assertEqual(self.invite().status_code, status.HTTP_201_CREATED)
        token = self.invitation_token()
        from apps.tenancies.models import TenantInvitation
        invitation = TenantInvitation.objects.first()
        invitation.expires_at = timezone.now() - timedelta(seconds=1)
        invitation.save(update_fields=["expires_at"])
        self.client.force_authenticate(self.tenant)
        response = self.client.post("/api/v1/invitations/accept", {"token": token}, format="json")
        self.assertEqual(response.status_code, status.HTTP_410_GONE)
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, TenantInvitation.Status.EXPIRED)

    def test_invalid_dates_are_rejected_and_adjacent_tenancies_do_not_overlap(self):
        from datetime import date
        self.create_property_and_unit()
        start = date.today() + timedelta(days=10)
        bad = self.invite(start_date=start, end_date=start)
        self.assertEqual(bad.status_code, status.HTTP_400_BAD_REQUEST)
        first_end = start + timedelta(days=30)
        self.assertEqual(self.invite(start_date=start, end_date=first_end).status_code, status.HTTP_201_CREATED)
        first_token = self.invitation_token()
        self.client.force_authenticate(self.tenant)
        self.assertEqual(self.client.post("/api/v1/invitations/accept", {"token": first_token}, format="json").status_code, status.HTTP_200_OK)
        self.client.force_authenticate(self.owner)
        adjacent = self.invite(email="other-tenant@example.test", start_date=first_end, end_date=first_end + timedelta(days=30))
        self.assertEqual(adjacent.status_code, status.HTTP_201_CREATED, adjacent.data)

    def test_overlapping_tenancy_is_rejected(self):
        self.create_property_and_unit()
        first = self.invite()
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        from datetime import date
        overlap = self.invite(email="other-tenant@example.test", start_date=date.today() + timedelta(days=3))
        self.assertEqual(overlap.status_code, status.HTTP_409_CONFLICT)

    def test_property_archive_is_blocked_by_open_invitation(self):
        self.create_property_and_unit()
        self.assertEqual(self.invite().status_code, status.HTTP_201_CREATED)
        response = self.client.post(f"/api/v1/properties/{self.property_id}/archive", {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_building_and_unit_must_belong_to_same_property(self):
        self.create_property_and_unit()
        other_property = self.client.post(f"/api/v1/organizations/{self.org.id}/properties", {"name": "Other"}, format="json")
        self.assertEqual(other_property.status_code, status.HTTP_201_CREATED)
        building = self.client.post(f"/api/v1/properties/{self.property_id}/buildings", {"name": "North"}, format="json")
        self.assertEqual(building.status_code, status.HTTP_201_CREATED, building.data)
        unit = self.client.patch(f"/api/v1/units/{self.unit_id}", {"building_id": building.data["id"]}, format="json")
        self.assertEqual(unit.status_code, status.HTTP_200_OK)
        invalid = self.client.post(f"/api/v1/properties/{other_property.data['id']}/units", {"code": "B-1", "building_id": building.data["id"]}, format="json")
        self.assertEqual(invalid.status_code, status.HTTP_400_BAD_REQUEST)

    def test_tenancy_history_remains_after_authorized_end(self):
        from datetime import date
        self.create_property_and_unit()
        yesterday = date.today() - timedelta(days=1)
        response = self.invite(start_date=yesterday)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        token = self.invitation_token()
        self.client.force_authenticate(self.tenant)
        accepted = self.client.post("/api/v1/invitations/accept", {"token": token}, format="json")
        self.assertEqual(accepted.status_code, status.HTTP_200_OK)
        self.client.force_authenticate(self.owner)
        ended = self.client.post(
            f"/api/v1/tenancies/{accepted.data['id']}/end",
            {"actual_end_date": date.today().isoformat(), "reason": "Administrative record"},
            format="json",
        )
        self.assertEqual(ended.status_code, status.HTTP_200_OK, ended.data)
        self.assertEqual(ended.data["status"], "ended")
        history = self.client.get("/api/v1/tenancies")
        self.assertTrue(any(item["id"] == accepted.data["id"] for item in history.data))
        archived = self.client.post(f"/api/v1/properties/{self.property_id}/archive", {}, format="json")
        self.assertEqual(archived.status_code, status.HTTP_200_OK)
        self.assertEqual(self.client.get(f"/api/v1/properties/{self.property_id}").status_code, status.HTTP_200_OK)

    def test_manager_can_manage_assigned_property_caretaker_is_read_only(self):
        self.create_property_and_unit()
        from apps.organizations.models import PropertyAssignment
        manager = User.objects.create_user("manager2@example.test", "Strong-pass-4321")
        caretaker = User.objects.create_user("caretaker2@example.test", "Strong-pass-4321")
        manager_response = self.client.post(f"/api/v1/organizations/{self.org.id}/members", {
            "email": manager.email, "role": "manager", "property_ids": [self.property_id],
        }, format="json")
        self.assertEqual(manager_response.status_code, status.HTTP_201_CREATED, manager_response.data)
        caretaker_response = self.client.post(f"/api/v1/organizations/{self.org.id}/members", {
            "email": caretaker.email, "role": "caretaker", "property_ids": [self.property_id],
        }, format="json")
        self.assertEqual(caretaker_response.status_code, status.HTTP_201_CREATED, caretaker_response.data)
        self.client.force_authenticate(manager)
        self.assertEqual(self.client.patch(f"/api/v1/properties/{self.property_id}", {"county": "Nairobi"}, format="json").status_code, status.HTTP_200_OK)
        self.client.force_authenticate(caretaker)
        self.assertEqual(self.client.get(f"/api/v1/units/{self.unit_id}").status_code, status.HTTP_200_OK)
        self.assertEqual(self.client.patch(f"/api/v1/units/{self.unit_id}", {"label": "Unauthorized"}, format="json").status_code, status.HTTP_404_NOT_FOUND)
        invitation = self.invite()
        self.assertEqual(invitation.status_code, status.HTTP_404_NOT_FOUND)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.delete(f"/api/v1/organizations/{self.org.id}/members/{caretaker_response.data['id']}").status_code, status.HTTP_204_NO_CONTENT)
        self.client.force_authenticate(caretaker)
        self.assertEqual(self.client.get(f"/api/v1/units/{self.unit_id}").status_code, status.HTTP_404_NOT_FOUND)
