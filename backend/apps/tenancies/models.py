import uuid

from django.conf import settings
from django.db import models

from apps.organizations.models import OrganizationMembership
from apps.properties.models import Unit


class ImmutableTermsQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError("Tenancy terms versions are immutable")

    def delete(self):
        raise ValueError("Tenancy terms versions are immutable")


class Tenancy(models.Model):
    class Status(models.TextChoices):
        INVITED = "invited", "Invited"
        ACCEPTED_PENDING_START = "accepted_pending_start", "Accepted, pending start"
        ACTIVE = "active", "Active"
        SCHEDULED_TO_END = "scheduled_to_end", "Scheduled to end"
        ENDED = "ended", "Ended"
        CANCELLED = "cancelled", "Cancelled"
        DECLINED = "declined", "Declined"
        EXPIRED = "expired", "Expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="tenancies")
    invited_email = models.EmailField()
    tenant = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="tenancies")
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.INVITED)
    start_date = models.DateField()
    # Date intervals use [start_date, end_date): the end date is exclusive.
    end_date = models.DateField(null=True, blank=True)
    actual_end_date = models.DateField(null=True, blank=True)
    agreed_rent = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="KES")
    due_day = models.PositiveSmallIntegerField()
    deposit_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    deposit_terms = models.TextField(blank=True)
    agreement_reference = models.CharField(max_length=200, blank=True)
    terms_version = models.PositiveIntegerField(default=1)
    accepted_terms_version = models.PositiveIntegerField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_tenancies")
    ended_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="ended_tenancies")
    ended_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__isnull=True) | models.Q(end_date__gt=models.F("start_date")),
                name="tenancy_end_after_start",
            ),
            models.CheckConstraint(condition=models.Q(actual_end_date__isnull=True) | models.Q(actual_end_date__gt=models.F("start_date")), name="tenancy_actual_end_after_start"),
            models.CheckConstraint(
                condition=models.Q(actual_end_date__isnull=True) | models.Q(end_date__isnull=True) | models.Q(actual_end_date__lte=models.F("end_date")),
                name="tenancy_actual_end_within_terms",
            ),
            models.CheckConstraint(condition=models.Q(agreed_rent__gt=0), name="tenancy_rent_positive"),
            models.CheckConstraint(condition=models.Q(due_day__gte=1, due_day__lte=31), name="tenancy_due_day_range"),
            models.CheckConstraint(condition=models.Q(deposit_amount__gte=0), name="tenancy_deposit_nonnegative"),
        ]
        indexes = [models.Index(fields=("unit", "status", "start_date")), models.Index(fields=("tenant", "status"))]
        ordering = ("-start_date", "-created_at")


class TenancyTermsVersion(models.Model):
    tenancy = models.ForeignKey(Tenancy, on_delete=models.PROTECT, related_name="terms_history")
    version = models.PositiveIntegerField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tenancy_terms_changes")
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    agreed_rent = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3)
    due_day = models.PositiveSmallIntegerField()
    deposit_amount = models.DecimalField(max_digits=12, decimal_places=2)
    deposit_terms = models.TextField(blank=True)
    agreement_reference = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("tenancy", "version"), name="uniq_tenancy_terms_version")]
        ordering = ("tenancy_id", "version")

    objects = ImmutableTermsQuerySet.as_manager()

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValueError("Tenancy terms versions are immutable")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Tenancy terms versions are immutable")


class TenantInvitation(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        CANCELLED = "cancelled", "Cancelled"
        DECLINED = "declined", "Declined"
        EXPIRED = "expired", "Expired"

    class DeliveryStatus(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenancy = models.OneToOneField(Tenancy, on_delete=models.PROTECT, related_name="invitation")
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="tenant_invitations")
    email = models.EmailField()
    token_hash = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    delivery_status = models.CharField(max_length=12, choices=DeliveryStatus.choices, default=DeliveryStatus.QUEUED)
    expires_at = models.DateTimeField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="sent_tenant_invitations")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("unit", "email"), condition=models.Q(status="pending"), name="uniq_pending_invite_unit_email")]
        indexes = [models.Index(fields=("status", "expires_at"))]
