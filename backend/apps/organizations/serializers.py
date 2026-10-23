from rest_framework import serializers

from .models import Organization, PropertyScope


class OrganizationSerializer(serializers.ModelSerializer):
    my_role = serializers.SerializerMethodField()

    class Meta:
        model = Organization
        fields = ("id", "name", "timezone", "currency", "is_active", "created_at", "my_role")
        read_only_fields = ("id", "timezone", "currency", "is_active", "created_at")

    def get_my_role(self, obj):
        request = self.context.get("request")
        if request is None or not request.user.is_authenticated:
            return ""
        return obj.memberships.filter(user=request.user, is_active=True).values_list("role", flat=True).first() or ""

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Organization name is required.")
        return value


class OrganizationMemberCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    role = serializers.ChoiceField(choices=("manager", "caretaker"))
    property_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False)

    def validate_email(self, value):
        return value.strip().lower()

    def validate_property_ids(self, value):
        if len(value) != len(set(value)):
            raise serializers.ValidationError("Property IDs must not repeat.")
        return value


class PropertyScopeSerializer(serializers.ModelSerializer):
    class Meta:
        model = PropertyScope
        fields = ("id", "name", "organization_id", "is_active", "created_at")
        read_only_fields = fields
