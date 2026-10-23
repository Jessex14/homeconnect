from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, ValidationError

from apps.audit.services import record_domain_event
from apps.organizations.models import OrganizationMembership

from .models import MaintenanceEvent, MaintenanceTicket

TRANSITIONS = {
    MaintenanceTicket.Status.SUBMITTED: {MaintenanceTicket.Status.TRIAGED, MaintenanceTicket.Status.CANCELLED},
    MaintenanceTicket.Status.TRIAGED: {MaintenanceTicket.Status.ASSIGNED, MaintenanceTicket.Status.IN_PROGRESS, MaintenanceTicket.Status.CANCELLED},
    MaintenanceTicket.Status.ASSIGNED: {MaintenanceTicket.Status.IN_PROGRESS, MaintenanceTicket.Status.WAITING_ON_TENANT, MaintenanceTicket.Status.RESOLVED, MaintenanceTicket.Status.CANCELLED},
    MaintenanceTicket.Status.IN_PROGRESS: {MaintenanceTicket.Status.WAITING_ON_TENANT, MaintenanceTicket.Status.RESOLVED, MaintenanceTicket.Status.CANCELLED},
    MaintenanceTicket.Status.WAITING_ON_TENANT: {MaintenanceTicket.Status.IN_PROGRESS, MaintenanceTicket.Status.CANCELLED},
    MaintenanceTicket.Status.RESOLVED: {MaintenanceTicket.Status.CLOSED, MaintenanceTicket.Status.REOPENED},
    MaintenanceTicket.Status.CLOSED: {MaintenanceTicket.Status.REOPENED},
    MaintenanceTicket.Status.REOPENED: {MaintenanceTicket.Status.TRIAGED, MaintenanceTicket.Status.ASSIGNED, MaintenanceTicket.Status.IN_PROGRESS, MaintenanceTicket.Status.CANCELLED},
    MaintenanceTicket.Status.CANCELLED: set(),
}


class InvalidTicketTransition(APIException):
    status_code = 409
    default_detail = "This ticket cannot make that status transition."
    default_code = "invalid_ticket_transition"


def active_membership(user, property_obj, roles):
    return OrganizationMembership.objects.filter(
        user=user,
        organization_id=property_obj.organization_id,
        role__in=roles,
        is_active=True,
        organization__is_active=True,
        property_assignments__property_scope=property_obj,
        property_assignments__is_active=True,
    ).first()


def is_owner(user, property_obj):
    return OrganizationMembership.objects.filter(
        user=user,
        organization_id=property_obj.organization_id,
        role=OrganizationMembership.Role.OWNER,
        is_active=True,
        organization__is_active=True,
    ).exists()


def is_manager(user, property_obj):
    return is_owner(user, property_obj) or active_membership(
        user, property_obj, [OrganizationMembership.Role.MANAGER]
    ) is not None


def staff_can_view(user, ticket):
    if is_owner(user, ticket.unit.property):
        return True
    manager = active_membership(user, ticket.unit.property, [OrganizationMembership.Role.MANAGER])
    if manager:
        return True
    caretaker = active_membership(user, ticket.unit.property, [OrganizationMembership.Role.CARETAKER])
    return bool(caretaker and ticket.assigned_to_id == caretaker.pk)


def visible_tickets(user):
    from django.db.models import Q

    assigned = OrganizationMembership.objects.filter(
        user=user,
        is_active=True,
        role__in=[OrganizationMembership.Role.MANAGER, OrganizationMembership.Role.CARETAKER],
        property_assignments__is_active=True,
        property_assignments__property_scope__organization__is_active=True,
    )
    owner_orgs = OrganizationMembership.objects.filter(
        user=user, role=OrganizationMembership.Role.OWNER, is_active=True, organization__is_active=True
    ).values("organization_id")
    return MaintenanceTicket.objects.filter(
        Q(tenant=user)
        | Q(unit__property__organization_id__in=owner_orgs)
        | Q(unit__property__assignments__membership__in=assigned.filter(role=OrganizationMembership.Role.MANAGER), unit__property__assignments__is_active=True)
        | Q(assigned_to__in=assigned.filter(role=OrganizationMembership.Role.CARETAKER))
    ).distinct()


@transaction.atomic
def transition_ticket(*, ticket_id, actor, new_status, closure_note="", request=None, tenant_action=False):
    try:
        # assigned_to is nullable; lock the ticket row only to avoid PostgreSQL
        # rejecting FOR UPDATE against the nullable side of that outer join.
        ticket = MaintenanceTicket.objects.select_for_update(of=("self",)).select_related(
            "unit__property__organization", "tenant", "assigned_to"
        ).get(pk=ticket_id)
    except MaintenanceTicket.DoesNotExist as exc:
        raise NotFound("Not found.") from exc

    old = ticket.status
    previous_closure_note = ticket.closure_note
    if tenant_action:
        if ticket.tenant_id != actor.pk or new_status != MaintenanceTicket.Status.REOPENED or old not in {
            MaintenanceTicket.Status.RESOLVED, MaintenanceTicket.Status.CLOSED
        }:
            raise NotFound("Not found.")
    elif not staff_can_view(actor, ticket):
        raise NotFound("Not found.")
    if new_status not in TRANSITIONS.get(old, set()):
        raise InvalidTicketTransition()
    if new_status == MaintenanceTicket.Status.ASSIGNED and not ticket.assigned_to_id:
        raise ValidationError({"status": "Assign the ticket to an active property member first."})
    if new_status == MaintenanceTicket.Status.CLOSED and not closure_note.strip():
        raise ValidationError({"closure_note": "A closure note is required."})

    ticket.status = new_status
    if new_status == MaintenanceTicket.Status.RESOLVED:
        ticket.resolved_at = timezone.now()
    if new_status == MaintenanceTicket.Status.REOPENED:
        ticket.resolved_at = None
        ticket.closed_at = None
    if new_status == MaintenanceTicket.Status.CLOSED:
        ticket.closed_at = timezone.now()
        ticket.closure_note = closure_note.strip()
    ticket.save(update_fields=("status", "resolved_at", "closed_at", "closure_note", "updated_at"))
    MaintenanceEvent.objects.create(
        ticket=ticket,
        actor=actor,
        event_type="status_transition",
        from_status=old,
        to_status=new_status,
        details={
            "closure_note": ticket.closure_note if new_status == MaintenanceTicket.Status.CLOSED else "",
            "previous_closure_note": previous_closure_note if new_status == MaintenanceTicket.Status.REOPENED else "",
        },
    )
    record_domain_event(
        actor=actor,
        organization=ticket.unit.property.organization,
        obj=ticket,
        action="maintenance.status_transition",
        request=request,
        changes={
            "status": {"from": old, "to": new_status},
            **({"closure_note": {"to": ticket.closure_note}} if new_status == MaintenanceTicket.Status.CLOSED else {}),
        },
    )
    return ticket


@transaction.atomic
def assign_ticket(*, ticket_id, actor, membership_id, request=None):
    try:
        ticket = MaintenanceTicket.objects.select_for_update(of=("self",)).select_related("unit__property__organization").get(pk=ticket_id)
    except MaintenanceTicket.DoesNotExist as exc:
        raise NotFound("Not found.") from exc
    prop = ticket.unit.property
    if not is_manager(actor, prop):
        raise NotFound("Not found.")
    if ticket.status in {MaintenanceTicket.Status.RESOLVED, MaintenanceTicket.Status.CLOSED, MaintenanceTicket.Status.CANCELLED}:
        raise InvalidTicketTransition("Reopen the ticket before assigning it again.")
    assignee = OrganizationMembership.objects.filter(
        pk=membership_id,
        organization_id=prop.organization_id,
        role__in=[OrganizationMembership.Role.MANAGER, OrganizationMembership.Role.CARETAKER],
        is_active=True,
        organization__is_active=True,
        property_assignments__property_scope=prop,
        property_assignments__is_active=True,
    ).first()
    if not assignee:
        raise ValidationError({"membership_id": "Choose an active manager or caretaker assigned to this property."})
    before = ticket.assigned_to_id
    ticket.assigned_to = assignee
    if ticket.status in {MaintenanceTicket.Status.SUBMITTED, MaintenanceTicket.Status.TRIAGED, MaintenanceTicket.Status.REOPENED}:
        old_status = ticket.status
        ticket.status = MaintenanceTicket.Status.ASSIGNED
    else:
        old_status = ticket.status
    ticket.save(update_fields=("assigned_to", "status", "updated_at"))
    MaintenanceEvent.objects.create(
        ticket=ticket,
        actor=actor,
        event_type="assignment",
        from_status=old_status,
        to_status=ticket.status,
        details={"from_membership_id": before, "to_membership_id": assignee.pk},
    )
    record_domain_event(
        actor=actor, organization=prop.organization, obj=ticket, action="maintenance.assigned", request=request,
        changes={"assigned_to_id": {"from": before, "to": assignee.pk}, "status": {"from": old_status, "to": ticket.status}},
    )
    return ticket
