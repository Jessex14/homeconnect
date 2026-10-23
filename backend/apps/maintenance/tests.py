from datetime import date
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.conf import settings
from django.core.files.base import ContentFile
from django.test import override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.audit.models import DomainAuditEvent
from apps.communications.models import NotificationIntent
from apps.organizations.models import Organization, OrganizationMembership, PropertyAssignment
from apps.properties.models import Unit
from apps.tenancies.models import Tenancy

from .models import MaintenanceAttachment, MaintenanceComment, MaintenanceEvent, MaintenanceTicket


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class MaintenanceWorkflowTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user("maint-owner@example.test", "Strong-pass-4321")
        self.tenant = User.objects.create_user("maint-tenant@example.test", "Strong-pass-4321")
        self.other_tenant = User.objects.create_user("maint-other@example.test", "Strong-pass-4321")
        self.caretaker = User.objects.create_user("maint-care@example.test", "Strong-pass-4321")
        self.org = Organization.objects.create(name="Maintenance Portfolio")
        self.owner_membership = OrganizationMembership.objects.create(
            user=self.owner, organization=self.org, role=OrganizationMembership.Role.OWNER
        )
        OrganizationMembership.objects.create(user=self.tenant, organization=self.org, role=OrganizationMembership.Role.TENANT)
        OrganizationMembership.objects.create(user=self.other_tenant, organization=self.org, role=OrganizationMembership.Role.TENANT)
        self.property = self.org.property_scopes.create(name="Repair House")
        self.unit = Unit.objects.create(property=self.property, code="U-1")
        caretaker_membership = OrganizationMembership.objects.create(
            user=self.caretaker, organization=self.org, role=OrganizationMembership.Role.CARETAKER
        )
        PropertyAssignment.objects.create(
            property_scope=self.property, membership=caretaker_membership, role=OrganizationMembership.Role.CARETAKER
        )
        self.caretaker_membership = caretaker_membership
        self.tenancy = Tenancy.objects.create(
            unit=self.unit,
            invited_email=self.tenant.email,
            tenant=self.tenant,
            status=Tenancy.Status.ACTIVE,
            start_date=date(2026, 1, 1),
            agreed_rent="25000.00",
            due_day=1,
            created_by=self.owner,
        )

    def submit_ticket(self):
        self.client.force_authenticate(self.tenant)
        response = self.client.post(
            "/api/v1/maintenance-tickets",
            {"unit_id": str(self.unit.pk), "category": "plumbing", "priority": "high", "description": "Kitchen sink is leaking."},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        return response.data["id"]

    def test_tenant_can_submit_own_ticket_and_audit_is_created(self):
        ticket_id = self.submit_ticket()
        ticket = MaintenanceTicket.objects.get(pk=ticket_id)
        self.assertEqual(ticket.tenant_id, self.tenant.pk)
        self.assertEqual(ticket.status, MaintenanceTicket.Status.SUBMITTED)
        self.assertTrue(MaintenanceEvent.objects.filter(ticket=ticket, event_type="submitted").exists())
        self.assertTrue(DomainAuditEvent.objects.filter(object_id=str(ticket.pk), action="maintenance.submitted").exists())
        intent = NotificationIntent.objects.get(object_id=str(ticket.pk), event_type="maintenance.submitted")
        self.assertEqual(intent.recipient_id, self.tenant.pk)
        self.assertEqual(intent.status, NotificationIntent.Status.AWAITING_POLICY)
        self.assertFalse(intent.channel)

    def test_maintenance_photo_upload_is_private_and_scoped_to_ticket_participants(self):
        storage = MaintenanceAttachment._meta.get_field("file").storage
        old_root = storage._location
        old_cached = {key: storage.__dict__.pop(key, None) for key in ("base_location", "location")}
        with TemporaryDirectory(dir=settings.BASE_DIR) as private_root:
            storage._location = private_root
            storage.__dict__.pop("base_location", None)
            storage.__dict__.pop("location", None)
            try:
                def sanitized(_upload):
                    image = ContentFile(b"normalized private image", name="normalized.jpg")
                    image.content_type = "image/jpeg"
                    image.image_width = 320
                    image.image_height = 200
                    image.sha256 = "a" * 64
                    return image

                self.client.force_authenticate(self.tenant)
                with patch("apps.maintenance.serializers.sanitize_maintenance_photo", side_effect=sanitized):
                    submitted = self.client.post(
                        "/api/v1/maintenance-tickets",
                        {
                            "unit_id": str(self.unit.pk), "category": "plumbing", "priority": "high",
                            "description": "Pipe is leaking under the sink.",
                            "image": SimpleUploadedFile("sink.jpg", b"original", content_type="image/jpeg"),
                        },
                        format="multipart",
                    )
                self.assertEqual(submitted.status_code, status.HTTP_201_CREATED, submitted.data)
                attachment = MaintenanceAttachment.objects.get(ticket_id=submitted.data["id"])
                self.assertEqual(submitted.data["attachments"][0]["id"], str(attachment.pk))
                endpoint = submitted.data["attachments"][0]["url"]
                self.client.force_authenticate(self.other_tenant)
                self.assertEqual(self.client.get(endpoint).status_code, status.HTTP_404_NOT_FOUND)
                self.client.force_authenticate(self.tenant)
                photo = self.client.get(endpoint)
                self.assertEqual(photo.status_code, status.HTTP_200_OK)
                self.assertEqual(photo["Cache-Control"], "private, no-store")
                self.assertEqual(photo["X-Content-Type-Options"], "nosniff")
                self.assertEqual(b"".join(photo.streaming_content), b"normalized private image")
                self.assertNotIn(str(attachment.file.path), endpoint)
            finally:
                storage._location = old_root
                storage.__dict__.pop("base_location", None)
                storage.__dict__.pop("location", None)
                for key, value in old_cached.items():
                    if value is not None:
                        storage.__dict__[key] = value

    def test_invalid_maintenance_photo_is_rejected_without_creating_ticket(self):
        self.client.force_authenticate(self.tenant)
        response = self.client.post(
            "/api/v1/maintenance-tickets",
            {
                "unit_id": str(self.unit.pk), "category": "plumbing", "priority": "normal",
                "description": "Please inspect this file.",
                "image": SimpleUploadedFile("fake.jpg", b"this is not an image", content_type="image/jpeg"),
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(MaintenanceTicket.objects.count(), 0)

    def test_non_tenant_cannot_open_ticket_for_another_tenant_unit(self):
        self.client.force_authenticate(self.other_tenant)
        response = self.client.post(
            "/api/v1/maintenance-tickets",
            {"unit_id": str(self.unit.pk), "description": "Not my unit"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_tenant_sees_own_tickets_only(self):
        ticket_id = self.submit_ticket()
        self.client.force_authenticate(self.other_tenant)
        self.assertEqual(self.client.get("/api/v1/maintenance-tickets").data, [])
        self.assertEqual(self.client.get(f"/api/v1/maintenance-tickets/{ticket_id}").status_code, status.HTTP_404_NOT_FOUND)

    def test_manager_assignment_is_limited_to_active_property_members(self):
        ticket_id = self.submit_ticket()
        self.client.force_authenticate(self.owner)
        detail = self.client.get(f"/api/v1/maintenance-tickets/{ticket_id}")
        self.assertTrue(detail.data["can_assign"])
        self.assertEqual(detail.data["assignees"], [{"id": self.caretaker_membership.pk, "name": self.caretaker.email, "role": "caretaker"}])
        other_membership = OrganizationMembership.objects.create(
            user=User.objects.create_user("unassigned-care@example.test", "Strong-pass-4321"),
            organization=self.org,
            role=OrganizationMembership.Role.CARETAKER,
        )
        self.client.force_authenticate(self.owner)
        response = self.client.post(
            f"/api/v1/maintenance-tickets/{ticket_id}/assign", {"membership_id": other_membership.pk}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        response = self.client.post(
            f"/api/v1/maintenance-tickets/{ticket_id}/assign", {"membership_id": self.caretaker_membership.pk}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["status"], MaintenanceTicket.Status.ASSIGNED)

    def test_ticket_cannot_enter_assigned_state_without_assignee(self):
        ticket_id = self.submit_ticket()
        self.client.force_authenticate(self.owner)
        self.client.post(
            f"/api/v1/maintenance-tickets/{ticket_id}/transition", {"status": "triaged"}, format="json"
        )
        response = self.client.post(
            f"/api/v1/maintenance-tickets/{ticket_id}/transition", {"status": "assigned"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_closed_ticket_must_be_reopened_before_reassignment(self):
        ticket_id = self.submit_ticket()
        self.client.force_authenticate(self.owner)
        for next_status in ("triaged", "in_progress", "resolved"):
            self.client.post(
                f"/api/v1/maintenance-tickets/{ticket_id}/transition", {"status": next_status}, format="json"
            )
        self.client.post(
            f"/api/v1/maintenance-tickets/{ticket_id}/transition",
            {"status": "closed", "closure_note": "Repair completed."},
            format="json",
        )
        response = self.client.post(
            f"/api/v1/maintenance-tickets/{ticket_id}/assign", {"membership_id": self.caretaker_membership.pk}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_caretaker_can_only_see_and_update_assigned_tickets(self):
        ticket_id = self.submit_ticket()
        self.client.force_authenticate(self.caretaker)
        self.assertEqual(self.client.get(f"/api/v1/maintenance-tickets/{ticket_id}").status_code, status.HTTP_404_NOT_FOUND)
        self.client.force_authenticate(self.owner)
        self.client.post(
            f"/api/v1/maintenance-tickets/{ticket_id}/assign", {"membership_id": self.caretaker_membership.pk}, format="json"
        )
        self.client.force_authenticate(self.caretaker)
        detail = self.client.get(f"/api/v1/maintenance-tickets/{ticket_id}")
        self.assertTrue(detail.data["staff"])
        self.assertIn("in_progress", detail.data["allowed_transitions"])
        self.assertNotIn("assigned", detail.data["allowed_transitions"])
        response = self.client.post(
            f"/api/v1/maintenance-tickets/{ticket_id}/transition", {"status": "in_progress"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["status"], MaintenanceTicket.Status.IN_PROGRESS)

    def test_tenant_cannot_read_or_create_internal_comments(self):
        ticket_id = self.submit_ticket()
        ticket = MaintenanceTicket.objects.get(pk=ticket_id)
        MaintenanceComment.objects.create(
            ticket=ticket, author=self.owner, body="Internal contractor note", visibility=MaintenanceComment.Visibility.INTERNAL
        )
        self.client.force_authenticate(self.tenant)
        detail = self.client.get(f"/api/v1/maintenance-tickets/{ticket_id}")
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.data["comments"], [])
        self.assertFalse(detail.data["staff"])
        self.assertFalse(detail.data["can_assign"])
        self.assertEqual(detail.data["assignees"], [])
        self.assertEqual(detail.data["allowed_transitions"], [])
        response = self.client.post(
            f"/api/v1/maintenance-tickets/{ticket_id}/comments",
            {"body": "Trying to hide this", "visibility": "internal"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_close_requires_note_and_history_is_immutable(self):
        ticket_id = self.submit_ticket()
        self.client.force_authenticate(self.owner)
        for next_status in ("triaged", "in_progress", "resolved"):
            response = self.client.post(
                f"/api/v1/maintenance-tickets/{ticket_id}/transition", {"status": next_status}, format="json"
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        response = self.client.post(
            f"/api/v1/maintenance-tickets/{ticket_id}/transition", {"status": "closed"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        response = self.client.post(
            f"/api/v1/maintenance-tickets/{ticket_id}/transition",
            {"status": "closed", "closure_note": "Leak repaired and tested."},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        ticket = MaintenanceTicket.objects.get(pk=ticket_id)
        self.assertEqual(ticket.closure_note, "Leak repaired and tested.")
        event = ticket.events.first()
        with self.assertRaises(ValueError):
            event.delete()

    def test_tenant_can_reopen_resolved_ticket_but_not_transition_other_states(self):
        ticket_id = self.submit_ticket()
        self.client.force_authenticate(self.owner)
        for next_status in ("triaged", "in_progress", "resolved"):
            self.client.post(
                f"/api/v1/maintenance-tickets/{ticket_id}/transition", {"status": next_status}, format="json"
            )
        self.client.post(
            f"/api/v1/maintenance-tickets/{ticket_id}/transition",
            {"status": "closed", "closure_note": "Leak repaired and tested."},
            format="json",
        )
        self.client.force_authenticate(self.tenant)
        response = self.client.post(
            f"/api/v1/maintenance-tickets/{ticket_id}/transition", {"status": "reopened"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["status"], MaintenanceTicket.Status.REOPENED)
        detail = self.client.get(f"/api/v1/maintenance-tickets/{ticket_id}")
        self.assertEqual(detail.data["ticket"]["closure_note"], "Leak repaired and tested.")

