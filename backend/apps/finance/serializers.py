from datetime import date
from decimal import Decimal

from rest_framework import serializers

from .models import MpesaMerchantAccount, PaymentRecord


class PaymentRecordCreateSerializer(serializers.Serializer):
    idempotency_key = serializers.UUIDField()
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))
    method = serializers.ChoiceField(choices=PaymentRecord.Method.choices)
    payment_date = serializers.DateField()
    reference = serializers.CharField(required=False, allow_blank=True, max_length=160)
    notes = serializers.CharField(required=False, allow_blank=True, max_length=1000)


class PaymentReversalSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=1000, trim_whitespace=True)

    def validate_reason(self, value):
        if not value:
            raise serializers.ValidationError("A reason is required to reverse a payment record.")
        return value


class MpesaMerchantSettingsSerializer(serializers.Serializer):
    environment = serializers.ChoiceField(choices=MpesaMerchantAccount.Environment.choices)
    merchant_type = serializers.ChoiceField(choices=MpesaMerchantAccount.MerchantType.choices)
    business_shortcode = serializers.RegexField(r"^\d{5,12}$", max_length=12)
    shortcode = serializers.RegexField(r"^\d{5,12}$", max_length=12)
    consumer_key = serializers.CharField(required=False, allow_blank=True, max_length=500, trim_whitespace=False)
    consumer_secret = serializers.CharField(required=False, allow_blank=True, max_length=500, trim_whitespace=False)
    passkey = serializers.CharField(required=False, allow_blank=True, max_length=1000, trim_whitespace=False)


class MpesaPaymentStartSerializer(serializers.Serializer):
    idempotency_key = serializers.UUIDField()
    amount = serializers.IntegerField(min_value=1, max_value=250000)
    phone_number = serializers.CharField(max_length=16)

    def validate_phone_number(self, value):
        phone = value.strip().replace(" ", "").replace("-", "")
        if phone.startswith("+"):
            phone = phone[1:]
        if phone.startswith("0") and len(phone) == 10:
            phone = "254" + phone[1:]
        if phone.startswith("7") or phone.startswith("1"):
            phone = "254" + phone
        if len(phone) != 12 or not phone.startswith("254") or not phone.isdigit():
            raise serializers.ValidationError("Enter a Kenyan M-Pesa number, such as 0712 345 678 or 254712345678.")
        if phone[3] not in {"1", "7"}:
            raise serializers.ValidationError("Enter a valid Kenyan mobile number.")
        return phone


class RentObligationGenerateSerializer(serializers.Serializer):
    period_month = serializers.RegexField(r"^\d{4}-(0[1-9]|1[0-2])$", max_length=7)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"), required=False)

    def validate_period_month(self, value):
        year, month = (int(part) for part in value.split("-"))
        return date(year, month, 1)


class RentAdjustmentCreateSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("-9999999999.99"))
    reason = serializers.CharField(max_length=1000, trim_whitespace=True)

    def validate(self, attrs):
        if attrs["amount"] == 0:
            raise serializers.ValidationError({"amount": "Enter an increase or a reduction greater than zero."})
        if not attrs["reason"]:
            raise serializers.ValidationError({"reason": "A reason is required for every rent adjustment."})
        return attrs


class PaymentAllocationCreateSerializer(serializers.Serializer):
    payment_id = serializers.UUIDField()
    idempotency_key = serializers.UUIDField()
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))


class PaymentAllocationReversalSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=1000, trim_whitespace=True)

    def validate_reason(self, value):
        if not value:
            raise serializers.ValidationError("A reason is required to reverse an allocation.")
        return value


class PaymentRecordReadSerializer(serializers.ModelSerializer):
    recorded_by_name = serializers.EmailField(source="recorded_by.email", read_only=True)
    reversed = serializers.SerializerMethodField()
    reversal_reason = serializers.SerializerMethodField()
    available_amount = serializers.SerializerMethodField()

    class Meta:
        model = PaymentRecord
        fields = ("id", "amount", "currency", "method", "payment_date", "reference", "notes", "source", "recorded_by_name", "created_at", "reversed", "reversal_reason", "available_amount")
        read_only_fields = fields

    def get_reversed(self, obj):
        return hasattr(obj, "reversal")

    def get_reversal_reason(self, obj):
        return obj.reversal.reason if hasattr(obj, "reversal") else ""

    def get_available_amount(self, obj):
        from .ledger import payment_available_amount
        return payment_available_amount(obj)
