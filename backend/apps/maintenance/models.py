import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.organizations.models import OrganizationMembership
from apps.properties.models import Unit

from .storage import PrivateMaintenanceStorage


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError("Maintenance history is append-only")

    def delete(self):
        raise ValueError("Maintenance history is append-only")


class MaintenanceTicket(models.Model):
    class Category(models.TextChoices):
        PLUMBING = "plumbing", "Plumbing"
        ELECTRICAL = "electrical", "Electrical"
        STRUCTURAL = "structural", "Structural"
        SECURITY = "security", "Security"
        APPLIANCE = "appliance", "Appliance"
        PEST = "pest", "Pest control"
        OTHER = "other", "Other"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        TRIAGED = "triaged", "Triaged"
        ASSIGNED = "assigned", "Assigned"
        IN_PROGRESS = "in_progress", "In progress"
        WAITING_ON_TENANT = "waiting_on_tenant", "Waiting on tenant"
        RESOLVED = "resolved", "Resolved"
        CLOSED = "closed", "Closed"
        REOPENED = "reopened", "Reopened"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="maintenance_tickets")
    tenant = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="maintenance_tickets")
    category = models.CharField(max_length=16, choices=Category.choices, default=Category.OTHER)
    priority = models.CharField(max_length=12, choices=Priority.choices, default=Priority.NORMAL)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.SUBMITTED)
    description = models.TextField(max_length=5000)
    preferred_contact = models.CharField(max_length=120, blank=True)
    assigned_to = models.ForeignKey(
        OrganizationMembership, null=True, blank=True, on_delete=models.PROTECT, related_name="assigned_maintenance_tickets"
    )
    closure_note = models.TextField(blank=True, max_length=2000)
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=("unit", "status", "created_at")),
            models.Index(fields=("tenant", "status", "created_at")),
            models.Index(fields=("assigned_to", "status", "created_at")),
        ]
        ordering = ("-created_at",)


class MaintenanceEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ticket = models.ForeignKey(MaintenanceTicket, on_delete=models.PROTECT, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)
    event_type = models.CharField(max_length=32)
    from_status = models.CharField(max_length=24, blank=True)
    to_status = models.CharField(max_length=24, blank=True)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at",)

    objects = AppendOnlyQuerySet.as_manager()

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValueError("Maintenance events are append-only")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Maintenance events are append-only")


class MaintenanceComment(models.Model):
    class Visibility(models.TextChoices):
        TENANT = "tenant", "Tenant visible"
        INTERNAL = "internal", "Internal"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ticket = models.ForeignKey(MaintenanceTicket, on_delete=models.PROTECT, related_name="comments")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="maintenance_comments")
    body = models.TextField(max_length=3000)
    visibility = models.CharField(max_length=12, choices=Visibility.choices, default=Visibility.TENANT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at",)

    objects = AppendOnlyQuerySet.as_manager()

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValueError("Maintenance comments are append-only")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Maintenance comments are append-only")


class MaintenanceAttachment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ticket = models.ForeignKey(MaintenanceTicket, on_delete=models.PROTECT, related_name="attachments")
    file = models.FileField(storage=PrivateMaintenanceStorage(), upload_to="maintenance/%Y/%m/")
    content_type = models.CharField(max_length=32)
    byte_size = models.PositiveIntegerField()
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="maintenance_attachments")
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ("created_at",)
        constraints = [models.CheckConstraint(condition=models.Q(byte_size__gt=0), name="maintenance_attachment_size_positive")]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValueError("Maintenance attachments are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Maintenance attachments cannot be deleted.")
