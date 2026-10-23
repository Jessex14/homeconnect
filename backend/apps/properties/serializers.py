from rest_framework import serializers

from apps.organizations.models import Property

from PIL import Image, UnidentifiedImageError
from rest_framework.exceptions import ValidationError

from .models import Building, RentalListing, RentalListingPhoto, Unit


class PropertyCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Property
        fields = ("name", "address", "county")

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Property name is required.")
        return value


class PropertyReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Property
        fields = ("id", "organization_id", "name", "address", "county", "status", "created_at")
        read_only_fields = fields


class PropertyUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Property
        fields = ("name", "address", "county")

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Property name is required.")
        return value


class BuildingSerializer(serializers.ModelSerializer):
    class Meta:
        model = Building
        fields = ("id", "property_id", "name", "created_at")
        read_only_fields = ("id", "property_id", "created_at")

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Building name is required.")
        return value


class BuildingUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Building
        fields = ("name",)

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Building name is required.")
        return value


class UnitSerializer(serializers.ModelSerializer):
    building_id = serializers.UUIDField(required=False, allow_null=True)

    class Meta:
        model = Unit
        fields = ("id", "property_id", "building_id", "code", "label", "unit_type", "status", "created_at")
        read_only_fields = ("id", "property_id", "created_at")

    def validate_code(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Unit code is required.")
        return value

    def validate_building_id(self, value):
        if value is None:
            return None
        property_obj = self.context["property"]
        if not Building.objects.filter(pk=value, property=property_obj).exists():
            raise serializers.ValidationError("Building must belong to this property.")
        return value

    def validate_status(self, value):
        if value in {Unit.Status.RESERVED, Unit.Status.OCCUPIED}:
            raise serializers.ValidationError("Reserved and occupied states are managed by tenancy workflows.")
        return value

    def create(self, validated_data):
        building_id = validated_data.pop("building_id", None)
        building = Building.objects.get(pk=building_id, property=self.context["property"]) if building_id else None
        return Unit.objects.create(property=self.context["property"], building=building, **validated_data)

    def update(self, instance, validated_data):
        building_id = validated_data.pop("building_id", serializers.empty)
        if building_id is not serializers.empty:
            instance.building = Building.objects.get(pk=building_id, property=instance.property) if building_id else None
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        return instance


class RentalListingWriteSerializer(serializers.ModelSerializer):
    photos = serializers.SerializerMethodField()

    class Meta:
        model = RentalListing
        fields = ("title", "description", "monthly_rent", "bedrooms", "bathrooms", "location_label", "contact_phone", "status", "photos")
        read_only_fields = ("photos",)

    def get_photos(self, obj):
        return RentalListingPhotoReadSerializer(obj.photos.all(), many=True, context=self.context).data

    def validate_title(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("A listing title is required.")
        return value

    def validate_monthly_rent(self, value):
        if value <= 0:
            raise serializers.ValidationError("Monthly rent must be greater than zero.")
        return value

    def validate_contact_phone(self, value):
        return value.strip()


class PublicRentalListingSerializer(serializers.ModelSerializer):
    county = serializers.CharField(source="unit.property.county", read_only=True)
    organization_name = serializers.CharField(source="unit.property.organization.name", read_only=True)
    unit_type = serializers.CharField(source="unit.unit_type", read_only=True)
    currency = serializers.CharField(source="unit.property.organization.currency", read_only=True)
    photos = serializers.SerializerMethodField()

    def get_photos(self, obj):
        return RentalListingPhotoReadSerializer(obj.photos.all(), many=True, context=self.context).data

    class Meta:
        model = RentalListing
        fields = ("id", "title", "description", "monthly_rent", "currency", "bedrooms", "bathrooms", "location_label", "contact_phone", "county", "organization_name", "unit_type", "published_at", "photos")


class RentalListingPhotoReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = RentalListingPhoto
        fields = ("id", "image", "alt_text", "sort_order", "created_at")
        read_only_fields = fields


class RentalListingPhotoUploadSerializer(serializers.ModelSerializer):
    class Meta:
        model = RentalListingPhoto
        fields = ("image", "alt_text")

    def validate_image(self, image):
        if image.size > 8 * 1024 * 1024:
            raise serializers.ValidationError("Each photo must be 8 MB or smaller.")
        try:
            image.seek(0)
            with Image.open(image) as picture:
                if picture.format not in {"JPEG", "PNG", "WEBP"}:
                    raise serializers.ValidationError("Use a JPG, PNG, or WebP image.")
                if picture.width * picture.height > 20_000_000:
                    raise serializers.ValidationError("Photo dimensions must be 20 megapixels or smaller.")
                picture.verify()
            image.seek(0)
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
            raise serializers.ValidationError("This file is not a valid image.")
        return image
