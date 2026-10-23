from decimal import Decimal

from rest_framework import serializers

from .models import Tenancy


class InvitationCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    start_date = serializers.DateField()
    end_date = serializers.DateField(required=False, allow_null=True)
    agreed_rent = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))
    due_day = serializers.IntegerField(min_value=1, max_value=31)
    deposit_amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.00"), default=Decimal("0.00"))
    deposit_terms = serializers.CharField(required=False, allow_blank=True, max_length=5000)
    agreement_reference = serializers.CharField(required=False, allow_blank=True, max_length=200)

    def validate_email(self, value):
        return value.strip().lower()

    def validate(self, attrs):
        end_date = attrs.get("end_date")
        if end_date is not None and end_date <= attrs["start_date"]:
            raise serializers.ValidationError({"end_date": "End date must be after the start date. The end date is exclusive."})
        return attrs


class TokenSerializer(serializers.Serializer):
    token = serializers.CharField(min_length=20, max_length=160, trim_whitespace=True)


class TenancyEndSerializer(serializers.Serializer):
    actual_end_date = serializers.DateField()
    reason = serializers.CharField(required=False, allow_blank=True, max_length=1000)


class TenancyReadSerializer(serializers.ModelSerializer):
    property_id = serializers.UUIDField(source="unit.property_id", read_only=True)
    property_name = serializers.CharField(source="unit.property.name", read_only=True)
    unit_code = serializers.CharField(source="unit.code", read_only=True)
    tenant_email = serializers.EmailField(source="tenant.email", read_only=True, allow_null=True)

    class Meta:
        model = Tenancy
        fields = (
            "id", "property_id", "property_name", "unit_id", "unit_code", "tenant_email", "invited_email",
            "status", "start_date", "end_date", "actual_end_date", "agreed_rent", "currency", "due_day",
            "deposit_amount", "deposit_terms", "agreement_reference", "terms_version", "accepted_terms_version",
            "accepted_at", "created_at",
        )
        read_only_fields = fields
