from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.services import record_domain_event
from apps.organizations.models import OrganizationMembership, Property
from apps.organizations.permissions import can_manage_property
from apps.properties.models import Unit

from .models import TenantInvitation, Tenancy
from .serializers import InvitationCreateSerializer, TenancyEndSerializer, TenancyReadSerializer, TokenSerializer
from .services import (
    create_terms_version,
    deliver_invitation,
    expire_stale_invites,
    invitation_expiry,
    new_invitation_token,
    refresh_unit_status,
    tenancy_conflict,
    token_digest,
)


def invitation_review_data(invitation):
    tenancy = invitation.tenancy
    return {
        "invitation_id": str(invitation.pk),
        "expires_at": invitation.expires_at,
        "property": {"id": str(tenancy.unit.property_id), "name": tenancy.unit.property.name},
        "unit": {"id": str(tenancy.unit_id), "code": tenancy.unit.code},
        "terms": {
            "start_date": tenancy.start_date,
            "end_date": tenancy.end_date,
            "end_date_is_exclusive": True,
            "agreed_rent": tenancy.agreed_rent,
            "currency": tenancy.currency,
            "due_day": tenancy.due_day,
            "deposit_amount": tenancy.deposit_amount,
            "deposit_terms": tenancy.deposit_terms,
            "agreement_reference": tenancy.agreement_reference,
            "terms_version": tenancy.terms_version,
        },
    }


def find_invitation(raw_token):
    query = TenantInvitation.objects.select_related("tenancy", "tenancy__unit", "tenancy__unit__property", "tenancy__unit__property__organization")
    return query.filter(token_hash=token_digest(raw_token)).first()


def lock_invitation(raw_token):
    candidate = find_invitation(raw_token)
    if candidate is None:
        return None, None
    # Shared lock order (property -> unit -> invitation -> tenancy) prevents deadlocks.
    Property.objects.select_for_update().get(pk=candidate.unit.property_id)
    unit = Unit.objects.select_for_update().select_related("property", "property__organization").get(pk=candidate.unit_id)
    invitation = TenantInvitation.objects.select_for_update().select_related("tenancy").get(pk=candidate.pk)
    if invitation.token_hash != token_digest(raw_token):
        return None, None
    return invitation, unit


def mark_expired(invitation, request):
    invitation.status = TenantInvitation.Status.EXPIRED
    invitation.token_hash = token_digest(new_invitation_token())
    invitation.save(update_fields=("status", "token_hash", "updated_at"))
    tenancy = invitation.tenancy
    tenancy.status = Tenancy.Status.EXPIRED
    tenancy.save(update_fields=("status", "updated_at"))
    refresh_unit_status(invitation.unit)
    record_domain_event(
        actor=None, organization=invitation.unit.property.organization, obj=tenancy,
        action="tenancy.invitation_expired", request=request,
        changes={"status": Tenancy.Status.EXPIRED},
    )


def invite_delivery_response(invitation, raw_token, request, created=False):
    sent = deliver_invitation(invitation, raw_token)
    record_domain_event(
        actor=request.user,
        organization=invitation.unit.property.organization,
        obj=invitation,
        action="tenancy.invitation_sent" if sent else "tenancy.invitation_delivery_failed",
        request=request,
        changes={"delivery_status": invitation.delivery_status},
    )
    payload = {
        "id": str(invitation.pk),
        "tenancy_id": str(invitation.tenancy_id),
        "status": invitation.status,
        "delivery_status": invitation.delivery_status,
        "expires_at": invitation.expires_at,
    }
    if not sent:
        payload["detail"] = "Invitation was recorded, but delivery failed. Retry delivery before the expiry time."
        return Response(payload, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    return Response(payload, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class UnitInvitationCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, unit_id):
        unit = get_object_or_404(Unit.objects.select_related("property__organization"), pk=unit_id)
        if not can_manage_property(request.user, unit.property):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        invitations = TenantInvitation.objects.filter(unit=unit).select_related("tenancy").order_by("-created_at")
        return Response([
            {
                "id": str(invitation.pk),
                "email": invitation.email,
                "status": invitation.status,
                "delivery_status": invitation.delivery_status,
                "expires_at": invitation.expires_at,
                "created_at": invitation.created_at,
                "start_date": invitation.tenancy.start_date,
                "end_date": invitation.tenancy.end_date,
            }
            for invitation in invitations
        ])

    def post(self, request, unit_id):
        serializer = InvitationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        with transaction.atomic():
            unit_ref = get_object_or_404(Unit.objects.only("property_id"), pk=unit_id)
            property_obj = get_object_or_404(Property.objects.select_for_update().select_related("organization"), pk=unit_ref.property_id)
            unit = get_object_or_404(Unit.objects.select_for_update(), pk=unit_id, property=property_obj)
            unit.property = property_obj
            if not can_manage_property(request.user, unit.property):
                return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
            if unit.property.status != Property.Status.ACTIVE or not unit.property.is_active:
                return Response({"detail": "Property is not active."}, status=status.HTTP_409_CONFLICT)
            if unit.status in (Unit.Status.DRAFT, Unit.Status.MAINTENANCE, Unit.Status.INACTIVE):
                return Response({"detail": "Unit is not available for tenancy."}, status=status.HTTP_409_CONFLICT)
            expire_stale_invites(unit, request)
            duplicate = TenantInvitation.objects.filter(
                unit=unit, email=data["email"], status=TenantInvitation.Status.PENDING,
            ).exists()
            if duplicate:
                return Response({"detail": "A pending invitation already exists for this unit and email."}, status=status.HTTP_409_CONFLICT)
            conflict = tenancy_conflict(unit, data["start_date"], data.get("end_date"))
            if conflict:
                return Response({"detail": "The requested dates overlap another open or historical tenancy."}, status=status.HTTP_409_CONFLICT)

            tenancy = Tenancy.objects.create(
                unit=unit,
                invited_email=data["email"],
                status=Tenancy.Status.INVITED,
                start_date=data["start_date"],
                end_date=data.get("end_date"),
                agreed_rent=data["agreed_rent"],
                currency=unit.property.organization.currency,
                due_day=data["due_day"],
                deposit_amount=data["deposit_amount"],
                deposit_terms=data.get("deposit_terms", ""),
                agreement_reference=data.get("agreement_reference", ""),
                terms_version=1,
                created_by=request.user,
            )
            create_terms_version(tenancy, request.user)
            token = new_invitation_token()
            invitation = TenantInvitation.objects.create(
                tenancy=tenancy,
                unit=unit,
                email=data["email"],
                token_hash=token_digest(token),
                expires_at=invitation_expiry(),
                created_by=request.user,
            )
            refresh_unit_status(unit)
            record_domain_event(
                actor=request.user, organization=unit.property.organization, obj=tenancy,
                action="tenancy.invited", request=request,
                changes={"status": tenancy.status, "terms_version": tenancy.terms_version},
            )
        return invite_delivery_response(invitation, token, request, created=True)


class InvitationReviewView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = TokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            invitation, unit = lock_invitation(serializer.validated_data["token"])
            if invitation is None or invitation.status != TenantInvitation.Status.PENDING:
                return Response({"detail": "Invitation not found or no longer available."}, status=status.HTTP_404_NOT_FOUND)
            if invitation.email.lower() != request.user.email.lower():
                return Response({"detail": "Invitation not found or no longer available."}, status=status.HTTP_404_NOT_FOUND)
            if invitation.expires_at <= timezone.now():
                mark_expired(invitation, request)
                return Response({"detail": "Invitation has expired."}, status=status.HTTP_410_GONE)
            return Response(invitation_review_data(invitation))


class InvitationAcceptView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = TokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            invitation, unit = lock_invitation(serializer.validated_data["token"])
            if invitation is None or invitation.status != TenantInvitation.Status.PENDING:
                return Response({"detail": "Invitation not found or no longer available."}, status=status.HTTP_404_NOT_FOUND)
            tenancy = Tenancy.objects.select_for_update().get(pk=invitation.tenancy_id)
            if invitation.email.lower() != request.user.email.lower():
                return Response({"detail": "Invitation not found or no longer available."}, status=status.HTTP_404_NOT_FOUND)
            if invitation.expires_at <= timezone.now():
                mark_expired(invitation, request)
                return Response({"detail": "Invitation has expired."}, status=status.HTTP_410_GONE)
            if tenancy_conflict(unit, tenancy.start_date, tenancy.end_date, exclude_tenancy_id=tenancy.pk):
                return Response({"detail": "The unit's tenancy dates are no longer available."}, status=status.HTTP_409_CONFLICT)
            today = timezone.localdate()
            if tenancy.end_date and tenancy.end_date <= today:
                mark_expired(invitation, request)
                return Response({"detail": "The proposed tenancy period has already ended."}, status=status.HTTP_410_GONE)
            tenancy.tenant = request.user
            tenancy.accepted_at = timezone.now()
            tenancy.accepted_terms_version = tenancy.terms_version
            tenancy.status = Tenancy.Status.ACCEPTED_PENDING_START if tenancy.start_date > today else (
                Tenancy.Status.SCHEDULED_TO_END if tenancy.end_date else Tenancy.Status.ACTIVE
            )
            tenancy.save(update_fields=("tenant", "accepted_at", "accepted_terms_version", "status", "updated_at"))
            invitation.status = TenantInvitation.Status.ACCEPTED
            invitation.token_hash = token_digest(new_invitation_token())
            invitation.save(update_fields=("status", "token_hash", "updated_at"))
            tenant_membership, _ = OrganizationMembership.objects.get_or_create(
                organization=unit.property.organization,
                user=request.user,
                role=OrganizationMembership.Role.TENANT,
                defaults={"is_active": True},
            )
            if not tenant_membership.is_active:
                tenant_membership.is_active = True
                tenant_membership.save(update_fields=("is_active",))
            unit.status = Unit.Status.OCCUPIED if tenancy.status in (Tenancy.Status.ACTIVE, Tenancy.Status.SCHEDULED_TO_END) else Unit.Status.RESERVED
            unit.save(update_fields=("status",))
            record_domain_event(
                actor=request.user, organization=unit.property.organization, obj=tenancy,
                action="tenancy.accepted", request=request,
                changes={"status": tenancy.status, "accepted_terms_version": tenancy.accepted_terms_version},
            )
        return Response(TenancyReadSerializer(tenancy).data)


class InvitationDeclineView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = TokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            invitation, unit = lock_invitation(serializer.validated_data["token"])
            if invitation is None or invitation.status != TenantInvitation.Status.PENDING:
                return Response({"detail": "Invitation not found or no longer available."}, status=status.HTTP_404_NOT_FOUND)
            if invitation.email.lower() != request.user.email.lower():
                return Response({"detail": "Invitation not found or no longer available."}, status=status.HTTP_404_NOT_FOUND)
            if invitation.expires_at <= timezone.now():
                mark_expired(invitation, request)
                return Response({"detail": "Invitation has expired."}, status=status.HTTP_410_GONE)
            invitation.status = TenantInvitation.Status.DECLINED
            invitation.token_hash = token_digest(new_invitation_token())
            invitation.save(update_fields=("status", "token_hash", "updated_at"))
            invitation.tenancy.status = Tenancy.Status.DECLINED
            invitation.tenancy.save(update_fields=("status", "updated_at"))
            refresh_unit_status(unit)
            record_domain_event(actor=request.user, organization=unit.property.organization, obj=invitation.tenancy, action="tenancy.declined", request=request, changes={"status": Tenancy.Status.DECLINED})
        return Response({"status": invitation.tenancy.status})


class InvitationResendView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, invitation_id):
        invitation = get_object_or_404(
            TenantInvitation.objects.select_related("unit", "unit__property", "unit__property__organization", "tenancy"),
            pk=invitation_id,
        )
        if not can_manage_property(request.user, invitation.unit.property):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        with transaction.atomic():
            Property.objects.select_for_update().get(pk=invitation.unit.property_id)
            unit = Unit.objects.select_for_update().get(pk=invitation.unit_id)
            invitation = TenantInvitation.objects.select_for_update().get(pk=invitation.pk)
            if invitation.status != TenantInvitation.Status.PENDING:
                return Response({"detail": "Only pending invitations can be resent."}, status=status.HTTP_409_CONFLICT)
            token = new_invitation_token()
            invitation.token_hash = token_digest(token)
            invitation.expires_at = invitation_expiry()
            invitation.delivery_status = TenantInvitation.DeliveryStatus.QUEUED
            invitation.save(update_fields=("token_hash", "expires_at", "delivery_status", "updated_at"))
        return invite_delivery_response(invitation, token, request)


class InvitationCancelView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, invitation_id):
        invitation = get_object_or_404(
            TenantInvitation.objects.select_related("unit", "unit__property", "unit__property__organization", "tenancy"),
            pk=invitation_id,
        )
        if not can_manage_property(request.user, invitation.unit.property):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        with transaction.atomic():
            Property.objects.select_for_update().get(pk=invitation.unit.property_id)
            unit = Unit.objects.select_for_update().get(pk=invitation.unit_id)
            invitation = TenantInvitation.objects.select_for_update().select_related("tenancy").get(pk=invitation.pk)
            if invitation.status != TenantInvitation.Status.PENDING:
                return Response({"detail": "Only pending invitations can be cancelled."}, status=status.HTTP_409_CONFLICT)
            invitation.status = TenantInvitation.Status.CANCELLED
            invitation.token_hash = token_digest(new_invitation_token())
            invitation.save(update_fields=("status", "token_hash", "updated_at"))
            invitation.tenancy.status = Tenancy.Status.CANCELLED
            invitation.tenancy.save(update_fields=("status", "updated_at"))
            refresh_unit_status(unit)
            record_domain_event(actor=request.user, organization=unit.property.organization, obj=invitation.tenancy, action="tenancy.invitation_cancelled", request=request, changes={"status": Tenancy.Status.CANCELLED})
        return Response({"id": str(invitation.pk), "status": invitation.status})


def managed_property_ids(user):
    owned = Property.objects.filter(
        organization__memberships__user=user,
        organization__memberships__role=OrganizationMembership.Role.OWNER,
        organization__memberships__is_active=True,
        organization__is_active=True,
    )
    assigned = Property.objects.filter(
        assignments__membership__user=user,
        assignments__membership__role=OrganizationMembership.Role.MANAGER,
        assignments__membership__is_active=True,
        assignments__is_active=True,
        organization__is_active=True,
    )
    return owned.union(assigned).values("pk")


def tenancy_response_data(tenancy, user):
    data = TenancyReadSerializer(tenancy).data
    manager = can_manage_property(user, tenancy.unit.property)
    today = timezone.localdate()
    data["can_manage"] = manager
    data["can_activate"] = bool(
        manager
        and tenancy.status == Tenancy.Status.ACCEPTED_PENDING_START
        and tenancy.start_date <= today
        and (tenancy.end_date is None or tenancy.end_date > today)
    )
    data["can_end"] = bool(
        manager and tenancy.status in (Tenancy.Status.ACTIVE, Tenancy.Status.SCHEDULED_TO_END)
    )
    return data


class TenancyListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        records = Tenancy.objects.select_related("unit", "unit__property", "tenant")
        records = records.filter(Q(tenant=request.user) | Q(unit__property_id__in=managed_property_ids(request.user))).distinct()
        return Response([tenancy_response_data(record, request.user) for record in records])


class TenancyDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, tenancy_id):
        tenancy = get_object_or_404(Tenancy.objects.select_related("unit", "unit__property", "tenant"), pk=tenancy_id)
        if tenancy.tenant_id != request.user.pk and not can_manage_property(request.user, tenancy.unit.property):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(tenancy_response_data(tenancy, request.user))


class TenancyActivateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, tenancy_id):
        tenancy = get_object_or_404(Tenancy.objects.select_related("unit", "unit__property", "unit__property__organization"), pk=tenancy_id)
        if not can_manage_property(request.user, tenancy.unit.property):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        with transaction.atomic():
            unit = Unit.objects.select_for_update().get(pk=tenancy.unit_id)
            tenancy = Tenancy.objects.select_for_update().get(pk=tenancy.pk)
            today = timezone.localdate()
            if tenancy.status != Tenancy.Status.ACCEPTED_PENDING_START:
                return Response({"detail": "Only an accepted future tenancy can be activated."}, status=status.HTTP_409_CONFLICT)
            if tenancy.start_date > today:
                return Response({"detail": "Tenancy cannot be activated before its start date."}, status=status.HTTP_409_CONFLICT)
            if tenancy.end_date and tenancy.end_date <= today:
                return Response({"detail": "The agreed tenancy period has ended; record an authorized termination."}, status=status.HTTP_409_CONFLICT)
            tenancy.status = Tenancy.Status.SCHEDULED_TO_END if tenancy.end_date else Tenancy.Status.ACTIVE
            tenancy.save(update_fields=("status", "updated_at"))
            unit.status = Unit.Status.OCCUPIED
            unit.save(update_fields=("status",))
            record_domain_event(actor=request.user, organization=unit.property.organization, obj=tenancy, action="tenancy.activated", request=request, changes={"status": tenancy.status})
        return Response(TenancyReadSerializer(tenancy).data)


class TenancyEndView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, tenancy_id):
        tenancy = get_object_or_404(Tenancy.objects.select_related("unit", "unit__property", "unit__property__organization"), pk=tenancy_id)
        if not can_manage_property(request.user, tenancy.unit.property):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        end_serializer = TenancyEndSerializer(data=request.data)
        end_serializer.is_valid(raise_exception=True)
        actual_end = end_serializer.validated_data["actual_end_date"]
        today = timezone.localdate()
        with transaction.atomic():
            unit = Unit.objects.select_for_update().get(pk=tenancy.unit_id)
            tenancy = Tenancy.objects.select_for_update().get(pk=tenancy.pk)
            if tenancy.status not in (Tenancy.Status.ACTIVE, Tenancy.Status.SCHEDULED_TO_END):
                return Response({"detail": "Only an active tenancy can be ended. Cancel an unstarted tenancy."}, status=status.HTTP_409_CONFLICT)
            if actual_end <= tenancy.start_date or actual_end > today:
                return Response({"actual_end_date": ["Effective end date must be after the start date and no later than today."]}, status=status.HTTP_400_BAD_REQUEST)
            if tenancy.end_date and actual_end > tenancy.end_date:
                return Response({"actual_end_date": ["Effective end date cannot be later than the agreed exclusive end date."]}, status=status.HTTP_400_BAD_REQUEST)
            tenancy.status = Tenancy.Status.ENDED
            tenancy.actual_end_date = actual_end
            tenancy.ended_by = request.user
            tenancy.ended_reason = end_serializer.validated_data.get("reason", "")
            tenancy.save(update_fields=("status", "actual_end_date", "ended_by", "ended_reason", "updated_at"))
            refresh_unit_status(unit)
            record_domain_event(
                actor=request.user, organization=unit.property.organization, obj=tenancy,
                action="tenancy.ended", request=request, reason=tenancy.ended_reason,
                changes={"status": tenancy.status, "actual_end_date": actual_end.isoformat()},
            )
        return Response(TenancyReadSerializer(tenancy).data)
