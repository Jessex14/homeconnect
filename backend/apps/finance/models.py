import uuid

from django.conf import settings
from django.db import models

from apps.tenancies.models import Tenancy
from apps.organizations.models import Organization


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError("Financial history is append-only.")

    def delete(self):
        raise ValueError("Financial history cannot be deleted.")


class PaymentRecord(models.Model):
    class Source(models.TextChoices):
        MANUAL = "manual", "Manual"
        MPESA = "mpesa", "M-Pesa"
    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        BANK_TRANSFER = "bank_transfer", "Bank transfer"
        MOBILE_MONEY = "mobile_money", "Mobile money"
        CHEQUE = "cheque", "Cheque"
        OTHER = "other", "Other"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenancy = models.ForeignKey(Tenancy, on_delete=models.PROTECT, related_name="payment_records")
    idempotency_key = models.UUIDField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3)
    method = models.CharField(max_length=20, choices=Method.choices)
    payment_date = models.DateField()
    reference = models.CharField(max_length=160, blank=True)
    notes = models.CharField(max_length=1000, blank=True)
    source = models.CharField(max_length=12, choices=Source.choices, default=Source.MANUAL)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="recorded_payments")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="payment_record_amount_positive"),
            models.UniqueConstraint(fields=("tenancy", "idempotency_key"), name="uniq_payment_idempotency_per_tenancy"),
            models.UniqueConstraint(fields=("reference",), condition=models.Q(source="mpesa") & ~models.Q(reference=""), name="uniq_mpesa_receipt_reference"),
        ]
        ordering = ("-payment_date", "-created_at")

    objects = models.Manager.from_queryset(AppendOnlyQuerySet)()

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValueError("Payment records cannot be changed; add a reversal instead.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Payment records cannot be deleted; add a reversal instead.")


class PaymentReversal(models.Model):
    payment = models.OneToOneField(PaymentRecord, on_delete=models.PROTECT, related_name="reversal")
    reason = models.CharField(max_length=1000)
    reversed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="payment_reversals")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = models.Manager.from_queryset(AppendOnlyQuerySet)()

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValueError("Payment reversals are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Payment reversals cannot be deleted.")


class RentObligation(models.Model):
    """One rent charge for a tenancy calendar month; the month is uniquely keyed."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenancy = models.ForeignKey(Tenancy, on_delete=models.PROTECT, related_name="rent_obligations")
    period_month = models.DateField(help_text="First day of the calendar month charged.")
    due_date = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_rent_obligations")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("tenancy", "period_month"), name="uniq_rent_obligation_month"),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="rent_obligation_amount_positive"),
        ]
        ordering = ("period_month", "created_at")

    objects = models.Manager.from_queryset(AppendOnlyQuerySet)()

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValueError("Rent obligations cannot be changed; add an adjustment instead.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Rent obligations cannot be deleted; add an adjustment instead.")


class RentAdjustment(models.Model):
    obligation = models.ForeignKey(RentObligation, on_delete=models.PROTECT, related_name="adjustments")
    amount = models.DecimalField(max_digits=12, decimal_places=2, help_text="Positive increases the charge; negative reduces it.")
    reason = models.CharField(max_length=1000)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="rent_adjustments")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=~models.Q(amount=0), name="rent_adjustment_nonzero")]
        ordering = ("created_at",)

    objects = models.Manager.from_queryset(AppendOnlyQuerySet)()

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValueError("Rent adjustments are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Rent adjustments cannot be deleted.")


class PaymentAllocation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payment = models.ForeignKey(PaymentRecord, on_delete=models.PROTECT, related_name="allocations")
    obligation = models.ForeignKey(RentObligation, on_delete=models.PROTECT, related_name="allocations")
    idempotency_key = models.UUIDField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="payment_allocations")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="payment_allocation_amount_positive"),
            models.UniqueConstraint(fields=("payment", "idempotency_key"), name="uniq_payment_allocation_idempotency"),
        ]
        ordering = ("created_at",)

    objects = models.Manager.from_queryset(AppendOnlyQuerySet)()

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValueError("Payment allocations are immutable; add a reversal instead.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Payment allocations cannot be deleted; add a reversal instead.")


class PaymentAllocationReversal(models.Model):
    allocation = models.OneToOneField(PaymentAllocation, on_delete=models.PROTECT, related_name="reversal")
    reason = models.CharField(max_length=1000)
    reversed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="payment_allocation_reversals")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = models.Manager.from_queryset(AppendOnlyQuerySet)()

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValueError("Payment allocation reversals are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Payment allocation reversals cannot be deleted.")


class MpesaMerchantAccount(models.Model):
    class Environment(models.TextChoices):
        SANDBOX = "sandbox", "Sandbox"
        PRODUCTION = "production", "Production"

    class MerchantType(models.TextChoices):
        PAYBILL = "paybill", "Paybill"
        TILL = "till", "Till / Buy Goods"

    organization = models.OneToOneField(Organization, on_delete=models.PROTECT, related_name="mpesa_account")
    environment = models.CharField(max_length=12, choices=Environment.choices, default=Environment.SANDBOX)
    merchant_type = models.CharField(max_length=12, choices=MerchantType.choices, default=MerchantType.PAYBILL)
    business_shortcode = models.CharField(max_length=12)
    shortcode = models.CharField(max_length=12)
    consumer_key_encrypted = models.TextField()
    consumer_secret_encrypted = models.TextField()
    passkey_encrypted = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def configured(self):
        return bool(self.shortcode and self.consumer_key_encrypted and self.consumer_secret_encrypted and self.passkey_encrypted)


class MpesaPaymentAttempt(models.Model):
    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        PROCESSING = "processing", "Processing"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenancy = models.ForeignKey(Tenancy, on_delete=models.PROTECT, related_name="mpesa_attempts")
    idempotency_key = models.UUIDField()
    callback_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    amount = models.PositiveIntegerField()
    phone_number = models.CharField(max_length=12)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.REQUESTED)
    merchant_request_id = models.CharField(max_length=100, blank=True)
    checkout_request_id = models.CharField(max_length=100, blank=True, unique=True, null=True)
    result_code = models.CharField(max_length=20, blank=True)
    result_description = models.CharField(max_length=500, blank=True)
    receipt_number = models.CharField(max_length=32, blank=True)
    transaction_date = models.DateTimeField(null=True, blank=True)
    payment_record = models.OneToOneField(PaymentRecord, null=True, blank=True, on_delete=models.PROTECT, related_name="mpesa_attempt")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("tenancy", "idempotency_key"), name="uniq_mpesa_attempt_idempotency")]
        ordering = ("-created_at",)
