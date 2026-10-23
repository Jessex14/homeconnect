from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.services import record_domain_event, record_security_event

from .models import Organization, OrganizationMembership, PropertyAssignment, PropertyScope, visible_property_scopes
from .serializers import OrganizationMemberCreateSerializer, OrganizationSerializer, PropertyScopeSerializer
from apps.accounts.models import User


class OrganizationListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organizations = Organization.objects.filter(
            memberships__user=request.user,
            memberships__is_active=True,
            is_active=True,
        ).distinct().order_by("name")
        return Response(OrganizationSerializer(organizations, many=True, context={"request": request}).data)

    def post(self, request):
        serializer = OrganizationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            organization = serializer.save()
            OrganizationMembership.objects.create(
                organization=organization,
                user=request.user,
                role=OrganizationMembership.Role.OWNER,
            )
            record_security_event("organization.created", request, actor=request.user)
        return Response(OrganizationSerializer(organization, context={"request": request}).data, status=status.HTTP_201_CREATED)


class OrganizationDetailView(RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = OrganizationSerializer
    lookup_field = "id"

    def get_queryset(self):
        return Organization.objects.filter(
            memberships__user=self.request.user,
            memberships__is_active=True,
            is_active=True,
        ).distinct()


class OrganizationMembersView(APIView):
    permission_classes = [IsAuthenticated]

    def get_organization(self, request, organization_id):
        return get_object_or_404(
            Organization.objects.filter(
                memberships__user=request.user,
                memberships__role=OrganizationMembership.Role.OWNER,
                memberships__is_active=True,
                is_active=True,
            ).distinct(),
            pk=organization_id,
        )

    def get(self, request, organization_id):
        organization = self.get_organization(request, organization_id)
        memberships = OrganizationMembership.objects.filter(organization=organization, is_active=True).select_related("user").order_by("role", "user__email")
        return Response([{
            "id": item.pk,
            "email": item.user.email,
            "role": item.role,
            "property_ids": list(item.property_assignments.filter(is_active=True).values_list("property_scope_id", flat=True)),
        } for item in memberships])

    def post(self, request, organization_id):
        organization = self.get_organization(request, organization_id)
        serializer = OrganizationMemberCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        user = User.objects.filter(email=data["email"]).first()
        if user is None:
            return Response({"email": ["Account must exist before it can be assigned a property role."]}, status=status.HTTP_400_BAD_REQUEST)
        with transaction.atomic():
            organization = Organization.objects.select_for_update().get(pk=organization.pk)
            properties = list(PropertyScope.objects.select_for_update().filter(organization=organization, pk__in=data["property_ids"]))
            if len(properties) != len(data["property_ids"]):
                return Response({"property_ids": ["Every property must belong to this organization."]}, status=status.HTTP_400_BAD_REQUEST)
            membership, created = OrganizationMembership.objects.get_or_create(
                organization=organization,
                user=user,
                role=data["role"],
                defaults={"is_active": True},
            )
            if membership.is_active and not created:
                return Response({"detail": "That role is already active for this account."}, status=status.HTTP_409_CONFLICT)
            if not membership.is_active:
                membership.is_active = True
                membership.save(update_fields=("is_active",))
            requested_ids = {str(prop.pk) for prop in properties}
            membership.property_assignments.filter(is_active=True).exclude(property_scope_id__in=requested_ids).update(is_active=False)
            for property_obj in properties:
                assignment, _ = PropertyAssignment.objects.get_or_create(
                    property_scope=property_obj,
                    membership=membership,
                    defaults={"role": data["role"], "is_active": True},
                )
                if not assignment.is_active or assignment.role != data["role"]:
                    assignment.role = data["role"]
                    assignment.is_active = True
                    assignment.save(update_fields=("role", "is_active"))
            record_domain_event(
                actor=request.user, organization=organization, obj=membership,
                action="organization.member_assigned", request=request,
                changes={"role": data["role"], "property_ids": sorted(requested_ids)},
            )
        return Response({"id": membership.pk, "email": user.email, "role": membership.role, "property_ids": sorted(requested_ids)}, status=status.HTTP_201_CREATED)


class OrganizationMemberDeactivateView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, organization_id, membership_id):
        organizations = Organization.objects.filter(
            memberships__user=request.user,
            memberships__role=OrganizationMembership.Role.OWNER,
            memberships__is_active=True,
            is_active=True,
        ).distinct()
        organization = get_object_or_404(organizations, pk=organization_id)
        with transaction.atomic():
            membership = get_object_or_404(OrganizationMembership.objects.select_for_update().select_related("user"), pk=membership_id, organization=organization)
            if membership.role == OrganizationMembership.Role.OWNER:
                return Response({"detail": "Owner membership cannot be deactivated through this endpoint."}, status=status.HTTP_409_CONFLICT)
            if membership.role == OrganizationMembership.Role.TENANT:
                return Response({"detail": "Tenant membership access is controlled by tenancy history."}, status=status.HTTP_409_CONFLICT)
            if not membership.is_active:
                return Response({"detail": "Membership is already inactive."}, status=status.HTTP_409_CONFLICT)
            membership.is_active = False
            membership.save(update_fields=("is_active",))
            membership.property_assignments.filter(is_active=True).update(is_active=False)
            record_domain_event(actor=request.user, organization=organization, obj=membership, action="organization.member_deactivated", request=request, changes={"is_active": False})
        return Response(status=status.HTTP_204_NO_CONTENT)


class PropertyScopeListView(ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = PropertyScopeSerializer

    def get_queryset(self):
        return visible_property_scopes(self.request.user)


class PropertyScopeDetailView(RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = PropertyScopeSerializer
    lookup_field = "id"

    def get_queryset(self):
        return visible_property_scopes(self.request.user)
