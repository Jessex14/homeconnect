import uuid

from django.conf import settings
from django.db import models


class Organization(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=180)
    timezone = models.CharField(max_length=64, default="Africa/Nairobi")
    currency = models.CharField(max_length=3, default="KES")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)


class OrganizationMembership(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        MANAGER = "manager", "Manager"
        CARETAKER = "caretaker", "Caretaker"
        TENANT = "tenant", "Tenant"

    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="organization_memberships")
    role = models.CharField(max_length=16, choices=Role.choices)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("organization", "user", "role"), name="uniq_org_user_role")]
        indexes = [models.Index(fields=("user", "is_active")), models.Index(fields=("organization", "role", "is_active"))]


class Property(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="property_scopes")
    name = models.CharField(max_length=180)
    address = models.CharField(max_length=250, blank=True)
    county = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=("organization", "is_active"))]


# Compatibility alias for the foundation's authorization anchor API/model imports.
PropertyScope = Property


class PropertyAssignment(models.Model):
    property_scope = models.ForeignKey(PropertyScope, on_delete=models.PROTECT, related_name="assignments")
    membership = models.ForeignKey(OrganizationMembership, on_delete=models.PROTECT, related_name="property_assignments")
    role = models.CharField(max_length=16, choices=[
        (OrganizationMembership.Role.MANAGER, "Manager"),
        (OrganizationMembership.Role.CARETAKER, "Caretaker"),
    ])
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("property_scope", "membership"), name="uniq_property_membership_assignment")]

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.membership_id and self.property_scope_id:
            membership_org = self.membership.organization_id
            property_org = self.property_scope.organization_id
            if membership_org != property_org or self.membership.role != self.role:
                raise ValidationError("Assignment role and organization must match the membership and property.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


def visible_property_scopes(user):
    memberships = OrganizationMembership.objects.filter(user=user, is_active=True)
    owned = models.Q(organization__memberships__in=memberships.filter(role=OrganizationMembership.Role.OWNER))
    assigned = models.Q(assignments__membership__in=memberships.filter(role__in=[OrganizationMembership.Role.MANAGER, OrganizationMembership.Role.CARETAKER]), assignments__is_active=True)
    tenant = models.Q(units__tenancies__tenant=user, units__tenancies__status__in=["accepted_pending_start", "active", "scheduled_to_end", "ended"])
    # A tenant sees a property only through their own accepted or historical tenancy.
    return Property.objects.filter(owned | assigned | tenant, organization__is_active=True).distinct()
