import logging
from decimal import Decimal, InvalidOperation

from django.db import models, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.services import record_domain_event
from apps.organizations.models import Organization, OrganizationMembership, Property, visible_property_scopes
from apps.organizations.permissions import can_manage_property, can_view_all_property_units, can_view_unit

from .models import Building, RentalListing, RentalListingPhoto, Unit
from .serializers import BuildingSerializer, BuildingUpdateSerializer, PropertyCreateSerializer, PropertyReadSerializer, PropertyUpdateSerializer, PublicRentalListingSerializer, RentalListingPhotoReadSerializer, RentalListingPhotoUploadSerializer, RentalListingWriteSerializer, UnitSerializer

logger = logging.getLogger(__name__)


class OrganizationPropertyListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, organization_id):
        organization = get_object_or_404(
            Organization.objects.filter(memberships__user=request.user, memberships__is_active=True, is_active=True).distinct(),
            pk=organization_id,
        )
        properties = visible_property_scopes(request.user).filter(organization=organization).order_by("name")
        return Response(PropertyReadSerializer(properties, many=True).data)

    def post(self, request, organization_id):
        organization = get_object_or_404(
            Organization.objects.filter(
                memberships__user=request.user,
                memberships__role=OrganizationMembership.Role.OWNER,
                memberships__is_active=True,
                is_active=True,
            ).distinct(),
            pk=organization_id,
        )
        serializer = PropertyCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            property_obj = serializer.save(organization=organization)
            record_domain_event(
                actor=request.user, organization=organization, obj=property_obj,
                action="property.created", request=request,
                changes={"name": property_obj.name, "status": property_obj.status},
            )
        return Response(PropertyReadSerializer(property_obj).data, status=status.HTTP_201_CREATED)


class PropertyDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, request, property_id):
        return get_object_or_404(visible_property_scopes(request.user).select_related("organization"), pk=property_id)

    def get(self, request, property_id):
        return Response(PropertyReadSerializer(self.get_object(request, property_id)).data)

    def patch(self, request, property_id):
        with transaction.atomic():
            property_obj = get_object_or_404(Property.objects.select_for_update().select_related("organization"), pk=property_id)
            if not can_manage_property(request.user, property_obj):
                return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
            if property_obj.status == Property.Status.ARCHIVED:
                return Response({"detail": "Archived properties cannot be edited."}, status=status.HTTP_409_CONFLICT)
            before = {key: getattr(property_obj, key) for key in ("name", "address", "county")}
            serializer = PropertyUpdateSerializer(property_obj, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            property_obj = serializer.save()
            changed = {key: {"from": before[key], "to": getattr(property_obj, key)} for key in before if before[key] != getattr(property_obj, key)}
            if changed:
                record_domain_event(actor=request.user, organization=property_obj.organization, obj=property_obj, action="property.updated", request=request, changes=changed)
        return Response(PropertyReadSerializer(property_obj).data)


class PropertyArchiveView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, property_id):
        with transaction.atomic():
            property_obj = get_object_or_404(Property.objects.select_for_update().select_related("organization"), pk=property_id)
            if not can_manage_property(request.user, property_obj):
                return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
            active_states = ("accepted_pending_start", "active", "scheduled_to_end")
            live_tenancy = property_obj.units.filter(tenancies__status__in=active_states).exists()
            live_invitation = property_obj.units.filter(tenant_invitations__status="pending", tenant_invitations__expires_at__gt=timezone.now()).exists()
            if live_tenancy or live_invitation:
                return Response({"detail": "Property has an open tenancy or invitation."}, status=status.HTTP_409_CONFLICT)
            property_obj.status = Property.Status.ARCHIVED
            property_obj.is_active = False
            property_obj.save(update_fields=("status", "is_active"))
            record_domain_event(actor=request.user, organization=property_obj.organization, obj=property_obj, action="property.archived", request=request)
        return Response(PropertyReadSerializer(property_obj).data)


class PropertyBuildingsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, property_id):
        property_obj = get_object_or_404(visible_property_scopes(request.user), pk=property_id)
        buildings = property_obj.buildings.all()
        if not can_view_all_property_units(request.user, property_obj):
            buildings = buildings.filter(units__tenancies__tenant=request.user, units__tenancies__status__in=("accepted_pending_start", "active", "scheduled_to_end", "ended")).distinct()
        return Response(BuildingSerializer(buildings, many=True).data)

    def post(self, request, property_id):
        with transaction.atomic():
            property_obj = get_object_or_404(Property.objects.select_for_update().select_related("organization"), pk=property_id)
            if not can_manage_property(request.user, property_obj):
                return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
            if property_obj.status != Property.Status.ACTIVE:
                return Response({"detail": "Only active properties can receive buildings."}, status=status.HTTP_409_CONFLICT)
            serializer = BuildingSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            if property_obj.buildings.filter(name=serializer.validated_data["name"]).exists():
                return Response({"name": ["Building name already exists for this property."]}, status=status.HTTP_409_CONFLICT)
            building = serializer.save(property=property_obj)
            record_domain_event(actor=request.user, organization=property_obj.organization, obj=building, action="building.created", request=request, changes={"name": building.name})
        return Response(BuildingSerializer(building).data, status=status.HTTP_201_CREATED)


class BuildingDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, request, building_id):
        building = get_object_or_404(Building.objects.select_related("property", "property__organization"), pk=building_id, property__in=visible_property_scopes(request.user))
        if not can_view_all_property_units(request.user, building.property) and not building.units.filter(tenancies__tenant=request.user, tenancies__status__in=("accepted_pending_start", "active", "scheduled_to_end", "ended")).exists():
            from django.http import Http404
            raise Http404
        return building

    def get(self, request, building_id):
        return Response(BuildingSerializer(self.get_object(request, building_id)).data)

    def patch(self, request, building_id):
        with transaction.atomic():
            building_ref = self.get_object(request, building_id)
            property_obj = get_object_or_404(Property.objects.select_for_update().select_related("organization"), pk=building_ref.property_id)
            if not can_manage_property(request.user, property_obj):
                return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
            if property_obj.status != Property.Status.ACTIVE:
                return Response({"detail": "Archived properties cannot be edited."}, status=status.HTTP_409_CONFLICT)
            building = get_object_or_404(Building.objects.select_for_update(), pk=building_id, property=property_obj)
            serializer = BuildingUpdateSerializer(building, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            name = serializer.validated_data.get("name", building.name)
            if property_obj.buildings.exclude(pk=building.pk).filter(name=name).exists():
                return Response({"name": ["Building name already exists for this property."]}, status=status.HTTP_409_CONFLICT)
            before = building.name
            building = serializer.save()
            if before != building.name:
                record_domain_event(actor=request.user, organization=property_obj.organization, obj=building, action="building.updated", request=request, changes={"name": {"from": before, "to": building.name}})
        return Response(BuildingSerializer(building).data)


class PropertyUnitsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, property_id):
        property_obj = get_object_or_404(visible_property_scopes(request.user), pk=property_id)
        units = property_obj.units.select_related("building").order_by("code")
        if not can_view_all_property_units(request.user, property_obj):
            units = units.filter(tenancies__tenant=request.user, tenancies__status__in=("accepted_pending_start", "active", "scheduled_to_end", "ended")).distinct()
        return Response(UnitSerializer(units, many=True, context={"property": property_obj}).data)

    def post(self, request, property_id):
        with transaction.atomic():
            property_obj = get_object_or_404(Property.objects.select_for_update().select_related("organization"), pk=property_id)
            if not can_manage_property(request.user, property_obj):
                return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
            if property_obj.status != Property.Status.ACTIVE:
                return Response({"detail": "Only active properties can receive units."}, status=status.HTTP_409_CONFLICT)
            serializer = UnitSerializer(data=request.data, context={"property": property_obj})
            serializer.is_valid(raise_exception=True)
            if property_obj.units.filter(code=serializer.validated_data["code"]).exists():
                return Response({"code": ["Unit code already exists for this property."]}, status=status.HTTP_409_CONFLICT)
            unit = serializer.save()
            record_domain_event(actor=request.user, organization=property_obj.organization, obj=unit, action="unit.created", request=request, changes={"code": unit.code, "status": unit.status})
        return Response(UnitSerializer(unit, context={"property": property_obj}).data, status=status.HTTP_201_CREATED)


class UnitDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, request, unit_id):
        unit = get_object_or_404(Unit.objects.select_related("property", "property__organization", "building"), pk=unit_id, property__in=visible_property_scopes(request.user))
        if not can_view_unit(request.user, unit):
            from django.http import Http404
            raise Http404
        return unit

    def get(self, request, unit_id):
        unit = self.get_object(request, unit_id)
        return Response(UnitSerializer(unit, context={"property": unit.property}).data)

    def patch(self, request, unit_id):
        with transaction.atomic():
            unit_ref = self.get_object(request, unit_id)
            property_obj = get_object_or_404(Property.objects.select_for_update().select_related("organization"), pk=unit_ref.property_id)
            if not can_manage_property(request.user, property_obj):
                return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
            if property_obj.status != Property.Status.ACTIVE or not property_obj.is_active:
                return Response({"detail": "Archived properties cannot be edited."}, status=status.HTTP_409_CONFLICT)
            unit = get_object_or_404(Unit.objects.select_for_update(of=("self",)).select_related("building"), pk=unit_id, property=property_obj)
            serializer = UnitSerializer(unit, data=request.data, partial=True, context={"property": property_obj})
            serializer.is_valid(raise_exception=True)
            code = serializer.validated_data.get("code", unit.code)
            if property_obj.units.exclude(pk=unit.pk).filter(code=code).exists():
                return Response({"code": ["Unit code already exists for this property."]}, status=status.HTTP_409_CONFLICT)
            before = {key: getattr(unit, key) for key in ("code", "label", "unit_type", "status", "building_id")}
            unit = serializer.save()
            changed = {key: {"from": str(before[key]) if before[key] is not None else None, "to": str(getattr(unit, key)) if getattr(unit, key) is not None else None} for key in before if before[key] != getattr(unit, key)}
            if changed:
                record_domain_event(actor=request.user, organization=property_obj.organization, obj=unit, action="unit.updated", request=request, changes=changed)
        return Response(UnitSerializer(unit, context={"property": property_obj}).data)


class UnitRentalListingView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, unit_id):
        unit = get_object_or_404(Unit.objects.select_related("property", "property__organization"), pk=unit_id)
        if not can_manage_property(request.user, unit.property):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        listing = RentalListing.objects.filter(unit=unit).first()
        if not listing:
            return Response({"exists": False})
        data = RentalListingWriteSerializer(listing, context={"request": request}).data
        data["exists"] = True
        return Response(data)

    def delete(self, request, unit_id):
        with transaction.atomic():
            unit = get_object_or_404(
                Unit.objects.select_for_update().select_related("property", "property__organization"),
                pk=unit_id,
            )
            if not can_manage_property(request.user, unit.property):
                return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
            listing = RentalListing.objects.select_for_update().filter(unit=unit).first()
            if listing is None:
                return Response({"detail": "Rental listing not found."}, status=status.HTTP_404_NOT_FOUND)

            photos = list(listing.photos.values_list("id", "image"))
            record_domain_event(
                actor=request.user,
                organization=unit.property.organization,
                obj=listing,
                action="rental_listing.deleted",
                request=request,
                changes={
                    "title": listing.title,
                    "status": listing.status,
                    "photo_ids": [str(photo_id) for photo_id, _name in photos],
                },
            )
            listing.delete()

        from django.core.files.storage import default_storage

        for photo_id, name in photos:
            if name:
                try:
                    default_storage.delete(name)
                except Exception:
                    logger.exception("Could not remove listing photo file after deleting photo %s", photo_id)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def post(self, request, unit_id):
        return self.save_listing(request, unit_id)

    def put(self, request, unit_id):
        return self.save_listing(request, unit_id)

    def save_listing(self, request, unit_id):
        with transaction.atomic():
            unit = get_object_or_404(Unit.objects.select_for_update().select_related("property", "property__organization"), pk=unit_id)
            if not can_manage_property(request.user, unit.property):
                return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
            if unit.property.status != Property.Status.ACTIVE or not unit.property.is_active:
                return Response({"detail": "Archived properties cannot be listed."}, status=status.HTTP_409_CONFLICT)
            listing = RentalListing.objects.filter(unit=unit).first()
            serializer = RentalListingWriteSerializer(listing, data=request.data, partial=listing is not None)
            serializer.is_valid(raise_exception=True)
            new_status = serializer.validated_data.get("status", listing.status if listing else RentalListing.Status.DRAFT)
            if new_status == RentalListing.Status.PUBLISHED:
                open_tenancy = unit.tenancies.filter(status__in=("invited", "accepted_pending_start", "active", "scheduled_to_end")).exists()
                live_invitation = unit.tenant_invitations.filter(status="pending", expires_at__gt=timezone.now()).exists()
                if unit.status != Unit.Status.AVAILABLE or open_tenancy or live_invitation:
                    return Response({"detail": "Only available units without an open tenancy or invitation can be published."}, status=status.HTTP_409_CONFLICT)
            tracked_fields = ("title", "description", "monthly_rent", "bedrooms", "bathrooms", "location_label", "status")
            before = {key: getattr(listing, key) for key in tracked_fields} if listing else {}
            saved = serializer.save(unit=unit)
            if saved.status == RentalListing.Status.PUBLISHED and not saved.published_at:
                saved.published_at = timezone.now()
                saved.save(update_fields=("published_at", "updated_at"))
            elif saved.status != RentalListing.Status.PUBLISHED and saved.published_at:
                saved.published_at = None
                saved.save(update_fields=("published_at", "updated_at"))
            changed = {
                key: {"from": str(before[key]) if before[key] is not None else None, "to": str(getattr(saved, key)) if getattr(saved, key) is not None else None}
                for key in tracked_fields
                if key in before and before[key] != getattr(saved, key)
            }
            if listing is None:
                changed = {key: str(getattr(saved, key)) if getattr(saved, key) is not None else None for key in tracked_fields}
            if changed:
                record_domain_event(
                    actor=request.user, organization=unit.property.organization, obj=saved,
                    action="rental_listing.published" if saved.status == RentalListing.Status.PUBLISHED and before.get("status") != RentalListing.Status.PUBLISHED else "rental_listing.updated" if listing else "rental_listing.created",
                    request=request, changes=changed,
                )
        response_status = status.HTTP_201_CREATED if listing is None else status.HTTP_200_OK
        return Response(RentalListingWriteSerializer(saved, context={"request": request}).data, status=response_status)


class UnitRentalListingPhotosView(APIView):
    permission_classes = [IsAuthenticated]

    def get_listing(self, request, unit_id):
        unit = get_object_or_404(Unit.objects.select_related("property", "property__organization"), pk=unit_id)
        if not can_manage_property(request.user, unit.property):
            return None
        return RentalListing.objects.filter(unit=unit).first()

    def post(self, request, unit_id):
        with transaction.atomic():
            unit = get_object_or_404(Unit.objects.select_for_update().select_related("property", "property__organization"), pk=unit_id)
            if not can_manage_property(request.user, unit.property):
                return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
            listing = get_object_or_404(RentalListing.objects.select_for_update(), unit=unit)
            if listing.photos.count() >= 8:
                return Response({"detail": "A listing can have up to 8 photos."}, status=status.HTTP_400_BAD_REQUEST)
            serializer = RentalListingPhotoUploadSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            photo = serializer.save(listing=listing, sort_order=listing.photos.count())
            record_domain_event(actor=request.user, organization=unit.property.organization, obj=listing, action="rental_listing.photo_added", request=request, changes={"photo_id": str(photo.id)})
        return Response(RentalListingPhotoReadSerializer(photo, context={"request": request}).data, status=status.HTTP_201_CREATED)


class RentalListingPhotoDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, photo_id):
        with transaction.atomic():
            photo = get_object_or_404(RentalListingPhoto.objects.select_related("listing", "listing__unit__property", "listing__unit__property__organization"), pk=photo_id)
            if not can_manage_property(request.user, photo.listing.unit.property):
                return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
            name = photo.image.name
            listing = photo.listing
            photo.delete()
            record_domain_event(actor=request.user, organization=listing.unit.property.organization, obj=listing, action="rental_listing.photo_removed", request=request, changes={"photo_id": str(photo_id)})
        if name:
            from django.core.files.storage import default_storage
            default_storage.delete(name)
        return Response(status=status.HTTP_204_NO_CONTENT)


class PublicRentalListingListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        listings = RentalListing.objects.filter(
            status=RentalListing.Status.PUBLISHED,
            unit__status=Unit.Status.AVAILABLE,
            unit__property__status=Property.Status.ACTIVE,
            unit__property__is_active=True,
            unit__property__organization__is_active=True,
        ).exclude(unit__tenancies__status__in=("invited", "accepted_pending_start", "active", "scheduled_to_end"))
        county = request.query_params.get("county", "").strip()
        search = request.query_params.get("q", "").strip()
        home_type = request.query_params.get("home_type", "").strip()
        bedrooms = request.query_params.get("bedrooms", "").strip()
        min_rent = request.query_params.get("min_rent", "").strip()
        max_rent = request.query_params.get("max_rent", "").strip()
        if county:
            listings = listings.filter(unit__property__county__icontains=county)
        if search:
            listings = listings.filter(
                models.Q(title__icontains=search) | models.Q(description__icontains=search)
                | models.Q(location_label__icontains=search) | models.Q(unit__property__county__icontains=search)
                | models.Q(unit__unit_type__icontains=search)
            )
        if home_type:
            listings = listings.filter(unit__unit_type__icontains=home_type)
        if bedrooms:
            try:
                bedroom_count = int(bedrooms)
            except ValueError:
                return Response({"bedrooms": ["Enter a whole number."]}, status=status.HTTP_400_BAD_REQUEST)
            listings = listings.filter(bedrooms__gte=5) if bedroom_count >= 5 else listings.filter(bedrooms=bedroom_count)
        try:
            minimum = Decimal(min_rent) if min_rent else None
            maximum = Decimal(max_rent) if max_rent else None
        except (InvalidOperation, ValueError):
            return Response({"detail": "Rent filters must be valid numbers."}, status=status.HTTP_400_BAD_REQUEST)
        if (minimum is not None and not minimum.is_finite()) or (maximum is not None and not maximum.is_finite()):
            return Response({"detail": "Rent filters must be finite numbers."}, status=status.HTTP_400_BAD_REQUEST)
        if (minimum is not None and minimum < 0) or (maximum is not None and maximum < 0):
            return Response({"detail": "Rent filters cannot be negative."}, status=status.HTTP_400_BAD_REQUEST)
        if minimum is not None:
            listings = listings.filter(monthly_rent__gte=minimum)
        if maximum is not None:
            listings = listings.filter(monthly_rent__lte=maximum)
        listings = listings.select_related("unit", "unit__property", "unit__property__organization").order_by("-published_at", "-created_at")[:100]
        return Response(PublicRentalListingSerializer(listings, many=True, context={"request": request}).data)

    def patch(self, request, unit_id):
        with transaction.atomic():
            property_id = get_object_or_404(Unit.objects.only("property_id"), pk=unit_id).property_id
            property_obj = get_object_or_404(Property.objects.select_for_update().select_related("organization"), pk=property_id)
            # `building` is nullable, so PostgreSQL cannot lock every joined row
            # from this outer join. Only the unit row needs to be locked here.
            unit = get_object_or_404(Unit.objects.select_for_update(of=("self",)).select_related("building"), pk=unit_id, property=property_obj)
            if not can_view_unit(request.user, unit):
                return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
            if not can_manage_property(request.user, unit.property):
                return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
            if unit.property.status != Property.Status.ACTIVE:
                return Response({"detail": "Archived properties cannot be edited."}, status=status.HTTP_409_CONFLICT)
            if "status" in request.data and unit.tenancies.filter(status__in=("invited", "accepted_pending_start", "active", "scheduled_to_end")).exists():
                return Response({"detail": "Unit status cannot change while a tenancy or invitation is open."}, status=status.HTTP_409_CONFLICT)
            before = {key: getattr(unit, key) for key in ("code", "label", "unit_type", "status", "building_id")}
            serializer = UnitSerializer(unit, data=request.data, partial=True, context={"property": unit.property})
            serializer.is_valid(raise_exception=True)
            if "code" in serializer.validated_data and unit.property.units.exclude(pk=unit.pk).filter(code=serializer.validated_data["code"]).exists():
                return Response({"code": ["Unit code already exists for this property."]}, status=status.HTTP_409_CONFLICT)
            unit = serializer.save()
            changed = {key: {"from": before[key], "to": getattr(unit, key)} for key in before if before[key] != getattr(unit, key)}
            if changed:
                record_domain_event(actor=request.user, organization=unit.property.organization, obj=unit, action="unit.updated", request=request, changes=changed)
        return Response(UnitSerializer(unit, context={"property": unit.property}).data)
