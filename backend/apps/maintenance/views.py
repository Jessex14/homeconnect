from django.db import transaction
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.services import record_domain_event
from apps.communications.services import enqueue_notification
from apps.organizations.models import OrganizationMembership
from apps.properties.models import Unit
from apps.tenancies.models import Tenancy

from .models import MaintenanceAttachment, MaintenanceComment, MaintenanceEvent, MaintenanceTicket
from .serializers import (
    MaintenanceAssignSerializer,
    MaintenanceCommentCreateSerializer,
    MaintenanceCommentReadSerializer,
    MaintenanceTicketCreateSerializer,
    MaintenanceTicketReadSerializer,
    MaintenanceTransitionSerializer,
)
from .services import TRANSITIONS, assign_ticket, is_manager, staff_can_view, transition_ticket, visible_tickets


class MaintenanceTicketListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tickets = visible_tickets(request.user).select_related("unit", "unit__property", "tenant", "assigned_to").prefetch_related("attachments")
        return Response(MaintenanceTicketReadSerializer(tickets, many=True).data)

    def post(self, request):
        serializer = MaintenanceTicketCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        unit = get_object_or_404(
            Unit.objects.select_related("property__organization"), pk=serializer.validated_data["unit_id"]
        )
        active_tenancy = Tenancy.objects.filter(
            unit=unit,
            tenant=request.user,
            status__in=(Tenancy.Status.ACTIVE, Tenancy.Status.SCHEDULED_TO_END),
        ).exists()
        if not active_tenancy:
            raise NotFound("Not found.")
        image = serializer.validated_data.pop("image", None)
        attachment = None
        try:
            with transaction.atomic():
                ticket = serializer.save(unit=unit, tenant=request.user)
                if image:
                    attachment = MaintenanceAttachment.objects.create(
                        ticket=ticket, file=image, content_type=image.content_type, byte_size=image.size,
                        width=image.image_width, height=image.image_height, sha256=image.sha256, uploaded_by=request.user,
                    )
                MaintenanceEvent.objects.create(ticket=ticket, actor=request.user, event_type="submitted", to_status=ticket.status)
                record_domain_event(
                    actor=request.user,
                    organization=unit.property.organization,
                    obj=ticket,
                    action="maintenance.submitted",
                    request=request,
                    changes={"status": {"to": ticket.status}, "category": ticket.category, "priority": ticket.priority, "photo_attached": bool(image)},
                )
                enqueue_notification(
                    recipient=request.user,
                    event_type="maintenance.submitted",
                    template_key="maintenance.ticket_submitted",
                    object_type="maintenance_ticket",
                    object_id=ticket.pk,
                    dedupe_key=f"maintenance:{ticket.pk}:submitted:{request.user.pk}",
                )
        except Exception:
            if attachment:
                attachment.file.delete(save=False)
            raise
        return Response(MaintenanceTicketReadSerializer(ticket).data, status=status.HTTP_201_CREATED)


class MaintenanceTicketDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get_ticket(self, request, ticket_id):
        ticket = get_object_or_404(
            visible_tickets(request.user).select_related("unit", "unit__property", "unit__property__organization", "tenant", "assigned_to").prefetch_related("attachments"),
            pk=ticket_id,
        )
        return ticket

    def get(self, request, ticket_id):
        ticket = self.get_ticket(request, ticket_id)
        staff = staff_can_view(request.user, ticket)
        comments = ticket.comments.select_related("author", "author__profile")
        events = ticket.events.all()
        if not staff:
            comments = comments.filter(visibility=MaintenanceComment.Visibility.TENANT)
            events = events.filter(event_type="status_transition")
        allowed_transitions = sorted(TRANSITIONS.get(ticket.status, set())) if staff else (
            [MaintenanceTicket.Status.REOPENED]
            if ticket.tenant_id == request.user.pk and ticket.status in {MaintenanceTicket.Status.RESOLVED, MaintenanceTicket.Status.CLOSED}
            else []
        )
        if not ticket.assigned_to_id:
            allowed_transitions = [value for value in allowed_transitions if value != MaintenanceTicket.Status.ASSIGNED]
        can_assign = is_manager(request.user, ticket.unit.property)
        assignees = []
        if can_assign:
            assignments = OrganizationMembership.objects.filter(
                organization_id=ticket.unit.property.organization_id,
                role__in=(OrganizationMembership.Role.MANAGER, OrganizationMembership.Role.CARETAKER),
                is_active=True,
                organization__is_active=True,
                property_assignments__property_scope=ticket.unit.property,
                property_assignments__is_active=True,
            ).select_related("user__profile").distinct().order_by("user__email")
            assignees = [
                {
                    "id": membership.pk,
                    "name": membership.user.profile.display_name or membership.user.email,
                    "role": membership.role,
                }
                for membership in assignments
            ]
        return Response({
            "ticket": MaintenanceTicketReadSerializer(ticket).data,
            "staff": staff,
            "can_assign": can_assign,
            "assignees": assignees,
            "allowed_transitions": allowed_transitions,
            "comments": MaintenanceCommentReadSerializer(comments, many=True).data,
            "events": [
                {
                    "event_type": event.event_type,
                    "from_status": event.from_status,
                    "to_status": event.to_status,
                    "created_at": event.created_at,
                    "closure_note": event.details.get("closure_note", ""),
                    "previous_closure_note": event.details.get("previous_closure_note", ""),
                }
                for event in events
            ],
        })


class MaintenanceAttachmentView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, attachment_id):
        attachment = get_object_or_404(
            MaintenanceAttachment.objects.select_related("ticket__unit__property", "ticket__assigned_to"), pk=attachment_id,
        )
        if attachment.ticket.tenant_id != request.user.pk and not staff_can_view(request.user, attachment.ticket):
            raise NotFound("Not found.")
        extension = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}.get(attachment.content_type)
        if not extension:
            raise NotFound("Not found.")
        response = FileResponse(
            attachment.file.open("rb"), content_type=attachment.content_type, as_attachment=False,
            filename=f"maintenance-photo-{attachment.pk}.{extension}",
        )
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "private, no-store"
        response["Content-Security-Policy"] = "default-src 'none'; sandbox"
        return response


class MaintenanceAssignView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, ticket_id):
        serializer = MaintenanceAssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ticket = assign_ticket(
            ticket_id=ticket_id,
            actor=request.user,
            membership_id=serializer.validated_data["membership_id"],
            request=request,
        )
        return Response(MaintenanceTicketReadSerializer(ticket).data)


class MaintenanceTransitionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, ticket_id):
        serializer = MaintenanceTransitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ticket_ref = get_object_or_404(
            visible_tickets(request.user).select_related("unit__property"), pk=ticket_id
        )
        is_tenant = ticket_ref.tenant_id == request.user.pk and not staff_can_view(request.user, ticket_ref)
        ticket = transition_ticket(
            ticket_id=ticket_id,
            actor=request.user,
            new_status=serializer.validated_data["status"],
            closure_note=serializer.validated_data.get("closure_note", ""),
            request=request,
            tenant_action=is_tenant,
        )
        return Response(MaintenanceTicketReadSerializer(ticket).data)


class MaintenanceCommentListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, ticket_id):
        ticket = get_object_or_404(visible_tickets(request.user), pk=ticket_id)
        comments = ticket.comments.select_related("author", "author__profile")
        if not staff_can_view(request.user, ticket):
            comments = comments.filter(visibility=MaintenanceComment.Visibility.TENANT)
        return Response(MaintenanceCommentReadSerializer(comments, many=True).data)

    def post(self, request, ticket_id):
        ticket = get_object_or_404(
            visible_tickets(request.user).select_related("unit__property__organization", "tenant"), pk=ticket_id
        )
        staff = staff_can_view(request.user, ticket)
        if not staff and ticket.tenant_id != request.user.pk:
            raise NotFound("Not found.")
        serializer = MaintenanceCommentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        visibility = serializer.validated_data.get("visibility", MaintenanceComment.Visibility.TENANT)
        if visibility == MaintenanceComment.Visibility.INTERNAL and not staff:
            raise NotFound("Not found.")
        with transaction.atomic():
            comment = MaintenanceComment.objects.create(
                ticket=ticket,
                author=request.user,
                body=serializer.validated_data["body"],
                visibility=visibility,
            )
            MaintenanceEvent.objects.create(
                ticket=ticket,
                actor=request.user,
                event_type="comment_added",
                details={"visibility": visibility},
            )
            record_domain_event(
                actor=request.user,
                organization=ticket.unit.property.organization,
                obj=ticket,
                action="maintenance.comment_added",
                request=request,
                changes={"comment_visibility": visibility},
            )
        return Response(MaintenanceCommentReadSerializer(comment).data, status=status.HTTP_201_CREATED)

