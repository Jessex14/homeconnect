from rest_framework import serializers

from .models import MaintenanceComment, MaintenanceTicket
from .uploads import InvalidMaintenancePhoto, sanitize_maintenance_photo


class MaintenanceTicketCreateSerializer(serializers.ModelSerializer):
    unit_id = serializers.UUIDField()
    image = serializers.FileField(required=False, write_only=True, allow_empty_file=False)

    class Meta:
        model = MaintenanceTicket
        fields = ("unit_id", "category", "priority", "description", "preferred_contact", "image")

    def validate_description(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Describe the maintenance issue.")
        return value

    def validate_preferred_contact(self, value):
        return value.strip()

    def validate_image(self, value):
        try:
            return sanitize_maintenance_photo(value)
        except InvalidMaintenancePhoto as exc:
            raise serializers.ValidationError(str(exc)) from exc


class MaintenanceTicketReadSerializer(serializers.ModelSerializer):
    unit_id = serializers.UUIDField(read_only=True)
    unit_code = serializers.CharField(source="unit.code", read_only=True)
    property_id = serializers.UUIDField(source="unit.property_id", read_only=True)
    property_name = serializers.CharField(source="unit.property.name", read_only=True)
    tenant_name = serializers.CharField(source="tenant.profile.display_name", read_only=True)
    assigned_to_id = serializers.IntegerField(read_only=True)
    assigned_to_name = serializers.SerializerMethodField()
    assigned_to_role = serializers.CharField(source="assigned_to.role", read_only=True, allow_null=True)
    attachments = serializers.SerializerMethodField()

    def get_assigned_to_name(self, obj):
        if not obj.assigned_to_id:
            return ""
        user = obj.assigned_to.user
        return user.profile.display_name or user.email

    def get_attachments(self, obj):
        return [
            {
                "id": str(item.pk),
                "url": f"/api/v1/maintenance-attachments/{item.pk}",
                "content_type": item.content_type,
                "width": item.width,
                "height": item.height,
                "created_at": item.created_at,
            }
            for item in obj.attachments.all()
        ]

    class Meta:
        model = MaintenanceTicket
        fields = (
            "id", "unit_id", "unit_code", "property_id", "property_name", "tenant_name",
            "category", "priority", "status", "description", "preferred_contact", "assigned_to_id", "assigned_to_name", "assigned_to_role",
            "closure_note", "resolved_at", "closed_at", "created_at", "updated_at", "attachments",
        )
        read_only_fields = fields


class MaintenanceAssignSerializer(serializers.Serializer):
    membership_id = serializers.IntegerField()


class MaintenanceTransitionSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=MaintenanceTicket.Status.choices)
    closure_note = serializers.CharField(max_length=2000, required=False, allow_blank=True, trim_whitespace=True)


class MaintenanceCommentCreateSerializer(serializers.Serializer):
    body = serializers.CharField(max_length=3000, trim_whitespace=True)
    visibility = serializers.ChoiceField(choices=MaintenanceComment.Visibility.choices, required=False)

    def validate_body(self, value):
        if not value:
            raise serializers.ValidationError("Comment cannot be blank.")
        return value


class MaintenanceCommentReadSerializer(serializers.ModelSerializer):
    author_name = serializers.CharField(source="author.display_name", read_only=True)

    class Meta:
        model = MaintenanceComment
        fields = ("id", "author_name", "body", "visibility", "created_at")
