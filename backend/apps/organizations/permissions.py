from rest_framework.permissions import BasePermission

from .models import OrganizationMembership, visible_property_scopes


class IsOwnerOrAssignedPropertyMember(BasePermission):
    """Default-deny object policy for the foundation's property authorization anchor."""

    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False
        return visible_property_scopes(request.user).filter(pk=obj.pk).exists()


def has_org_role(user, organization_id, roles):
    return OrganizationMembership.objects.filter(
        user=user, organization_id=organization_id, role__in=roles, is_active=True, organization__is_active=True
    ).exists()


def can_manage_property(user, property_obj):
    if not user or not user.is_authenticated or not property_obj.organization.is_active:
        return False
    if has_org_role(user, property_obj.organization_id, [OrganizationMembership.Role.OWNER]):
        return True
    return property_obj.assignments.filter(
        membership__user=user,
        membership__role=OrganizationMembership.Role.MANAGER,
        membership__is_active=True,
        is_active=True,
    ).exists()


def can_view_property(user, property_obj):
    if not user or not user.is_authenticated:
        return False
    return visible_property_scopes(user).filter(pk=property_obj.pk).exists()


def can_view_all_property_units(user, property_obj):
    if has_org_role(user, property_obj.organization_id, [OrganizationMembership.Role.OWNER]):
        return True
    return property_obj.assignments.filter(
        membership__user=user,
        membership__role__in=[OrganizationMembership.Role.MANAGER, OrganizationMembership.Role.CARETAKER],
        membership__is_active=True,
        is_active=True,
    ).exists()


def can_view_unit(user, unit):
    if can_view_all_property_units(user, unit.property):
        return True
    return unit.tenancies.filter(
        tenant=user,
        status__in=["accepted_pending_start", "active", "scheduled_to_end", "ended"],
    ).exists()
