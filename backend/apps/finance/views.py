from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.services import record_domain_event
from apps.organizations.permissions import can_manage_property
from apps.organizations.models import Organization, OrganizationMembership
from apps.tenancies.models import Tenancy

from .models import MpesaMerchantAccount, MpesaPaymentAttempt, PaymentAllocation, PaymentRecord, PaymentReversal, RentAdjustment, RentObligation
from .ledger import (
    add_rent_adjustment,
    allocate_payment,
    effective_tenancy_end,
    generate_month_obligation,
    obligation_balance,
    payment_available_amount,
    reverse_allocation,
)
from .mpesa import MpesaGatewayError, decrypt_secret, encrypt_secret, initiate_stk_push
from .serializers import (
    MpesaMerchantSettingsSerializer, MpesaPaymentStartSerializer, PaymentAllocationCreateSerializer,
    PaymentAllocationReversalSerializer, PaymentRecordCreateSerializer, PaymentRecordReadSerializer,
    PaymentReversalSerializer, RentAdjustmentCreateSerializer, RentObligationGenerateSerializer,
)


def payment_queryset():
    return PaymentRecord.objects.select_related("recorded_by", "tenancy", "tenancy__tenant", "tenancy__unit", "tenancy__unit__property", "tenancy__unit__property__organization").prefetch_related("reversal", "allocations__reversal")


def rent_obligation_data(obligation, can_manage):
    balance = obligation_balance(obligation)
    end_date = effective_tenancy_end(obligation.tenancy)
    month_end = date(obligation.period_month.year + (obligation.period_month.month == 12), 1 if obligation.period_month.month == 12 else obligation.period_month.month + 1, 1)
    partial = obligation.tenancy.start_date > obligation.period_month or (end_date is not None and end_date < month_end)
    adjustments = [{
        "id": str(item.pk), "amount": str(item.amount), "reason": item.reason,
        "created_by": item.created_by.email, "created_at": item.created_at,
    } for item in obligation.adjustments.select_related("created_by").all()]
    allocations = [{
        "id": str(item.pk), "amount": str(item.amount), "payment_id": str(item.payment_id),
        "payment_date": item.payment.payment_date, "reference": item.payment.reference,
        "source": item.payment.source, "active": not hasattr(item, "reversal"),
        "reversal_reason": item.reversal.reason if hasattr(item, "reversal") else "",
    } for item in obligation.allocations.select_related("payment").prefetch_related("reversal").all()]
    return {
        "id": str(obligation.pk), "period_month": obligation.period_month.strftime("%Y-%m"),
        "due_date": obligation.due_date, "amount": str(obligation.amount), "currency": obligation.currency,
        "is_partial": partial, "required_amount": str(balance["required"]),
        "allocated_amount": str(balance["allocated"]), "outstanding_amount": str(balance["outstanding"]),
        "overdue": balance["outstanding"] > 0 and obligation.due_date < timezone.localdate(),
        "adjustments": adjustments, "allocations": allocations, "can_manage": can_manage,
    }


def django_validation_response(exc):
    return Response({"detail": " ".join(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)


class TenancyPaymentRecordsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, tenancy_id):
        tenancy = get_object_or_404(Tenancy.objects.select_related("tenant", "unit__property"), pk=tenancy_id)
        if tenancy.tenant_id != request.user.pk and not can_manage_property(request.user, tenancy.unit.property):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        records = payment_queryset().filter(tenancy=tenancy)
        payload = PaymentRecordReadSerializer(records, many=True).data
        for record in payload:
            record["record_type"] = "M-Pesa confirmed" if record["source"] == PaymentRecord.Source.MPESA else "Manual record — not provider-confirmed"
        return Response(payload)

    def post(self, request, tenancy_id):
        with transaction.atomic():
            tenancy = get_object_or_404(Tenancy.objects.select_for_update(of=("self",)).select_related("tenant", "unit__property", "unit__property__organization"), pk=tenancy_id)
            if not can_manage_property(request.user, tenancy.unit.property):
                return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
            serializer = PaymentRecordCreateSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            key = serializer.validated_data["idempotency_key"]
            existing = payment_queryset().filter(tenancy=tenancy, idempotency_key=key).first()
            if existing:
                return Response(PaymentRecordReadSerializer(existing).data, status=status.HTTP_200_OK)
            values = serializer.validated_data
            try:
                with transaction.atomic():
                    record = PaymentRecord.objects.create(
                        tenancy=tenancy, idempotency_key=key, amount=values["amount"], currency=tenancy.currency,
                        method=values["method"], payment_date=values["payment_date"], reference=values.get("reference", ""),
                        notes=values.get("notes", ""), recorded_by=request.user,
                    )
            except IntegrityError:
                record = payment_queryset().get(tenancy=tenancy, idempotency_key=key)
                return Response(PaymentRecordReadSerializer(record).data, status=status.HTTP_200_OK)
            record_domain_event(
                actor=request.user, organization=tenancy.unit.property.organization, obj=record,
                action="payment.manual_recorded", request=request,
                changes={"amount": str(record.amount), "currency": record.currency, "method": record.method, "payment_date": record.payment_date.isoformat()},
            )
        return Response(PaymentRecordReadSerializer(record).data, status=status.HTTP_201_CREATED)


class PaymentReceiptView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, payment_id):
        payment = get_object_or_404(payment_queryset(), pk=payment_id)
        tenancy = payment.tenancy
        manager = can_manage_property(request.user, tenancy.unit.property)
        if request.user.pk != tenancy.tenant_id and not manager:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        mpesa_attempt = getattr(payment, "mpesa_attempt", None)
        return TemplateResponse(request, "finance/payment_receipt.html", {
            "payment": payment,
            "tenancy": tenancy,
            "property": tenancy.unit.property,
            "organization": tenancy.unit.property.organization,
            "receipt_number": f"HC-{payment.pk.hex.upper()}",
            "is_provider_confirmed": payment.source == PaymentRecord.Source.MPESA,
            "provider_receipt": mpesa_attempt.receipt_number if mpesa_attempt else "",
            "reversal": getattr(payment, "reversal", None),
            "issued_at": payment.created_at,
        })


class PaymentRecordReverseView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, payment_id):
        serializer = PaymentReversalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            record = get_object_or_404(payment_queryset().select_for_update(of=("self",)), pk=payment_id)
            tenancy = record.tenancy
            if not can_manage_property(request.user, tenancy.unit.property):
                return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
            if record.source == PaymentRecord.Source.MPESA:
                return Response({"detail": "A confirmed M-Pesa payment must be reversed through Safaricom. This action only applies to manual records."}, status=status.HTTP_409_CONFLICT)
            if record.allocations.filter(reversal__isnull=True).exists():
                return Response({"detail": "Reverse the payment's rent allocations before reversing this payment."}, status=status.HTTP_409_CONFLICT)
            if hasattr(record, "reversal"):
                return Response({"detail": "This payment record has already been reversed."}, status=status.HTTP_409_CONFLICT)
            reversal = PaymentReversal.objects.create(payment=record, reason=serializer.validated_data["reason"], reversed_by=request.user)
            record_domain_event(
                actor=request.user, organization=tenancy.unit.property.organization, obj=record,
                action="payment.manual_record_reversed", request=request, reason=reversal.reason,
                changes={"reversed": True, "amount": str(record.amount)},
            )
        return Response({"payment_id": str(record.pk), "reversed": True, "reason": reversal.reason})


class TenancyRentObligationsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, tenancy_id):
        tenancy = get_object_or_404(Tenancy.objects.select_related("tenant", "unit__property"), pk=tenancy_id)
        manager = can_manage_property(request.user, tenancy.unit.property)
        if tenancy.tenant_id != request.user.pk and not manager:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        obligations = RentObligation.objects.filter(tenancy=tenancy).select_related("tenancy", "created_by").order_by("period_month")
        return Response([rent_obligation_data(item, manager) for item in obligations])

    def post(self, request, tenancy_id):
        tenancy = get_object_or_404(Tenancy.objects.select_related("unit__property", "unit__property__organization"), pk=tenancy_id)
        if not can_manage_property(request.user, tenancy.unit.property):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = RentObligationGenerateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                obligation, created, partial = generate_month_obligation(
                    tenancy=tenancy, month=serializer.validated_data["period_month"],
                    amount=serializer.validated_data.get("amount"), actor=request.user,
                )
                if created:
                    record_domain_event(
                        actor=request.user, organization=tenancy.unit.property.organization, obj=obligation,
                        action="rent.obligation_created", request=request,
                        changes={"period_month": obligation.period_month.strftime("%Y-%m"), "due_date": obligation.due_date.isoformat(), "amount": str(obligation.amount), "is_partial": partial},
                    )
        except DjangoValidationError as exc:
            return django_validation_response(exc)
        return Response(rent_obligation_data(obligation, True), status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class RentAdjustmentView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, obligation_id):
        obligation = get_object_or_404(RentObligation.objects.select_related("tenancy__unit__property", "tenancy__unit__property__organization"), pk=obligation_id)
        if not can_manage_property(request.user, obligation.tenancy.unit.property):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = RentAdjustmentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                adjustment = add_rent_adjustment(obligation=obligation, actor=request.user, **serializer.validated_data)
                record_domain_event(
                    actor=request.user, organization=obligation.tenancy.unit.property.organization, obj=adjustment,
                    action="rent.obligation_adjusted", request=request,
                    reason=adjustment.reason, changes={"amount": str(adjustment.amount), "obligation_id": str(obligation.pk)},
                )
        except DjangoValidationError as exc:
            return django_validation_response(exc)
        return Response(rent_obligation_data(obligation, True), status=status.HTTP_201_CREATED)


class RentAllocationView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, obligation_id):
        obligation = get_object_or_404(RentObligation.objects.select_related("tenancy__unit__property", "tenancy__unit__property__organization"), pk=obligation_id)
        if not can_manage_property(request.user, obligation.tenancy.unit.property):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = PaymentAllocationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payment = get_object_or_404(PaymentRecord.objects.filter(tenancy_id=obligation.tenancy_id), pk=serializer.validated_data["payment_id"])
        values = serializer.validated_data
        try:
            with transaction.atomic():
                allocation, created = allocate_payment(
                    payment=payment, obligation=obligation, actor=request.user,
                    idempotency_key=values["idempotency_key"], amount=values["amount"],
                )
                if created:
                    record_domain_event(
                        actor=request.user, organization=obligation.tenancy.unit.property.organization, obj=allocation,
                        action="rent.payment_allocated", request=request,
                        changes={"payment_id": str(payment.pk), "obligation_id": str(obligation.pk), "amount": str(allocation.amount)},
                    )
        except DjangoValidationError as exc:
            return django_validation_response(exc)
        return Response({"id": str(allocation.pk), "amount": str(allocation.amount), "created": created}, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class PaymentAllocationReverseView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, allocation_id):
        allocation = get_object_or_404(
            PaymentAllocation.objects.select_related("obligation__tenancy__unit__property", "obligation__tenancy__unit__property__organization"), pk=allocation_id,
        )
        if not can_manage_property(request.user, allocation.obligation.tenancy.unit.property):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = PaymentAllocationReversalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                allocation, reversal = reverse_allocation(allocation=allocation, actor=request.user, **serializer.validated_data)
                record_domain_event(
                    actor=request.user, organization=allocation.obligation.tenancy.unit.property.organization, obj=allocation,
                    action="rent.payment_allocation_reversed", request=request,
                    reason=reversal.reason, changes={"amount": str(allocation.amount), "payment_id": str(allocation.payment_id), "obligation_id": str(allocation.obligation_id)},
                )
        except DjangoValidationError as exc:
            return django_validation_response(exc)
        return Response({"allocation_id": str(allocation.pk), "reversed": True, "reason": reversal.reason})


class OrganizationMpesaSettingsView(APIView):
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
        account = MpesaMerchantAccount.objects.filter(organization=organization).first()
        return Response({
            "configured": bool(account and account.configured),
            "environment": account.environment if account else MpesaMerchantAccount.Environment.SANDBOX,
            "merchant_type": account.merchant_type if account else MpesaMerchantAccount.MerchantType.PAYBILL,
            "business_shortcode": account.business_shortcode if account else "",
            "shortcode": account.shortcode if account else "",
            "has_credentials": bool(account and account.configured),
            "callback_ready": settings.MPESA_CALLBACK_BASE_URL.startswith("https://"),
            "production_allowed": settings.MPESA_ALLOW_PRODUCTION,
        })

    def put(self, request, organization_id):
        organization = self.get_organization(request, organization_id)
        account = MpesaMerchantAccount.objects.filter(organization=organization).first()
        serializer = MpesaMerchantSettingsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = serializer.validated_data
        if values["environment"] == MpesaMerchantAccount.Environment.PRODUCTION and not settings.MPESA_ALLOW_PRODUCTION:
            return Response({"environment": ["Production M-Pesa is disabled until the host explicitly enables it."]}, status=status.HTTP_400_BAD_REQUEST)
        try:
            credentials = {
                field: values.get(field, "") or (decrypt_secret(getattr(account, f"{field}_encrypted")) if account else "")
                for field in ("consumer_key", "consumer_secret", "passkey")
            }
        except MpesaGatewayError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        missing = [field for field, value in credentials.items() if not value]
        if missing:
            return Response({field: ["This credential is required."] for field in missing}, status=status.HTTP_400_BAD_REQUEST)
        try:
            encrypted = {f"{field}_encrypted": encrypt_secret(value) for field, value in credentials.items()}
        except MpesaGatewayError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        if account is None:
            account = MpesaMerchantAccount(organization=organization)
        account.environment = values["environment"]
        account.merchant_type = values["merchant_type"]
        account.business_shortcode = values["business_shortcode"]
        account.shortcode = values["shortcode"]
        for field, value in encrypted.items():
            setattr(account, field, value)
        account.save()
        record_domain_event(
            actor=request.user, organization=organization, obj=account,
            action="payment.mpesa_account_configured", request=request,
            changes={"environment": account.environment, "merchant_type": account.merchant_type, "business_shortcode": account.business_shortcode, "shortcode": account.shortcode},
        )
        return self.get(request, organization_id)


class TenancyMpesaPaymentView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, tenancy_id):
        tenancy = get_object_or_404(
            Tenancy.objects.select_related("tenant", "unit__property__organization"),
            pk=tenancy_id,
            tenant=request.user,
            status__in=(Tenancy.Status.ACTIVE, Tenancy.Status.SCHEDULED_TO_END),
        )
        if tenancy.currency != "KES":
            return Response({"detail": "M-Pesa rent payments are currently available for KES tenancies only."}, status=status.HTTP_400_BAD_REQUEST)
        serializer = MpesaPaymentStartSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        organization = tenancy.unit.property.organization
        account = MpesaMerchantAccount.objects.filter(organization=organization).first()
        if account is None or not account.configured:
            return Response({"detail": "This landlord has not configured M-Pesa payments yet."}, status=status.HTTP_409_CONFLICT)
        if account.environment == MpesaMerchantAccount.Environment.PRODUCTION and not settings.MPESA_ALLOW_PRODUCTION:
            return Response({"detail": "Live M-Pesa payments are not enabled on this HomeConnect host."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        try:
            # Validate the public callback configuration before creating a request.
            from .mpesa import callback_url
            callback_url("00000000-0000-4000-8000-000000000000")
        except MpesaGatewayError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

        values = serializer.validated_data
        attempt = MpesaPaymentAttempt.objects.filter(tenancy=tenancy, idempotency_key=values["idempotency_key"]).first()
        if attempt:
            if attempt.amount != values["amount"] or attempt.phone_number != values["phone_number"]:
                return Response({"detail": "This request key has already been used for a different amount or phone number."}, status=status.HTTP_409_CONFLICT)
            return Response(self.serialize(attempt), status=status.HTTP_200_OK)
        try:
            with transaction.atomic():
                attempt = MpesaPaymentAttempt.objects.create(
                    tenancy=tenancy, idempotency_key=values["idempotency_key"], amount=values["amount"], phone_number=values["phone_number"],
                )
        except IntegrityError:
            attempt = MpesaPaymentAttempt.objects.get(tenancy=tenancy, idempotency_key=values["idempotency_key"])
            if attempt.amount != values["amount"] or attempt.phone_number != values["phone_number"]:
                return Response({"detail": "This request key has already been used for a different amount or phone number."}, status=status.HTTP_409_CONFLICT)
            return Response(self.serialize(attempt), status=status.HTTP_200_OK)
        try:
            response = initiate_stk_push(account, attempt)
        except MpesaGatewayError as exc:
            attempt.status = MpesaPaymentAttempt.Status.FAILED
            attempt.result_description = str(exc)[:500]
            attempt.save(update_fields=("status", "result_description", "updated_at"))
            return Response({"detail": str(exc), "attempt": self.serialize(attempt)}, status=status.HTTP_502_BAD_GATEWAY)
        attempt.status = MpesaPaymentAttempt.Status.PROCESSING
        attempt.merchant_request_id = str(response.get("MerchantRequestID", ""))[:100]
        attempt.checkout_request_id = str(response["CheckoutRequestID"])[:100]
        attempt.result_description = str(response.get("CustomerMessage") or response.get("ResponseDescription") or "Payment prompt sent.")[:500]
        attempt.save(update_fields=("status", "merchant_request_id", "checkout_request_id", "result_description", "updated_at"))
        record_domain_event(
            actor=request.user, organization=organization, obj=attempt,
            action="payment.mpesa_push_requested", request=request,
            changes={"amount": attempt.amount, "currency": tenancy.currency, "status": attempt.status},
        )
        return Response(self.serialize(attempt), status=status.HTTP_201_CREATED)

    @staticmethod
    def serialize(attempt):
        return {
            "id": str(attempt.pk), "amount": attempt.amount, "currency": attempt.tenancy.currency,
            "status": attempt.status, "message": attempt.result_description,
            "receipt_number": attempt.receipt_number,
        }


class MpesaPaymentStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, attempt_id):
        attempt = get_object_or_404(
            MpesaPaymentAttempt.objects.select_related("tenancy__tenant", "tenancy__unit__property"),
            pk=attempt_id,
        )
        if attempt.tenancy.tenant_id != request.user.pk and not can_manage_property(request.user, attempt.tenancy.unit.property):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(TenancyMpesaPaymentView.serialize(attempt))


class MpesaCallbackView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, callback_token):
        attempt = MpesaPaymentAttempt.objects.select_related("tenancy__unit__property__organization").filter(callback_token=callback_token).first()
        if attempt is None:
            return Response({"ResultCode": 0, "ResultDesc": "Accepted"})
        body = request.data.get("Body", {})
        callback = body.get("stkCallback", {}) if isinstance(body, dict) else {}
        checkout_id = str(callback.get("CheckoutRequestID", ""))
        merchant_id = str(callback.get("MerchantRequestID", ""))
        if not checkout_id or not attempt.checkout_request_id or checkout_id != attempt.checkout_request_id:
            return Response({"ResultCode": 0, "ResultDesc": "Accepted"})
        if attempt.merchant_request_id and merchant_id != attempt.merchant_request_id:
            return Response({"ResultCode": 0, "ResultDesc": "Accepted"})
        if attempt.status in (MpesaPaymentAttempt.Status.SUCCEEDED, MpesaPaymentAttempt.Status.FAILED):
            return Response({"ResultCode": 0, "ResultDesc": "Accepted"})
        result_code = str(callback.get("ResultCode", ""))[:20]
        result_desc = str(callback.get("ResultDesc", ""))[:500]
        if result_code != "0":
            attempt.status = MpesaPaymentAttempt.Status.FAILED
            attempt.result_code = result_code
            attempt.result_description = result_desc or "M-Pesa payment was not completed."
            attempt.save(update_fields=("status", "result_code", "result_description", "updated_at"))
            record_domain_event(actor=None, organization=attempt.tenancy.unit.property.organization, obj=attempt, action="payment.mpesa_push_failed", request=request, changes={"result_code": result_code})
            return Response({"ResultCode": 0, "ResultDesc": "Accepted"})

        items = callback.get("CallbackMetadata", {}).get("Item", [])
        metadata = {str(item.get("Name")): item.get("Value") for item in items if isinstance(item, dict) and "Name" in item}
        receipt = str(metadata.get("MpesaReceiptNumber", ""))[:32]
        try:
            amount = Decimal(str(metadata.get("Amount")))
        except (InvalidOperation, TypeError, ValueError):
            amount = Decimal("0")
        phone = str(metadata.get("PhoneNumber", ""))
        if not receipt or amount != Decimal(attempt.amount) or phone != attempt.phone_number:
            attempt.result_code = result_code
            attempt.result_description = "M-Pesa callback received; details need reconciliation."
            attempt.save(update_fields=("result_code", "result_description", "updated_at"))
            return Response({"ResultCode": 0, "ResultDesc": "Accepted"})
        raw_date = str(metadata.get("TransactionDate", ""))
        try:
            transaction_date = timezone.make_aware(datetime.strptime(raw_date, "%Y%m%d%H%M%S"), ZoneInfo("Africa/Nairobi"))
        except ValueError:
            transaction_date = timezone.now()
        with transaction.atomic():
            attempt = MpesaPaymentAttempt.objects.select_for_update().get(pk=attempt.pk)
            if attempt.status == MpesaPaymentAttempt.Status.SUCCEEDED:
                return Response({"ResultCode": 0, "ResultDesc": "Accepted"})
            try:
                with transaction.atomic():
                    record = PaymentRecord.objects.create(
                        tenancy=attempt.tenancy,
                        idempotency_key=attempt.idempotency_key,
                        amount=attempt.amount,
                        currency=attempt.tenancy.currency,
                        method=PaymentRecord.Method.MOBILE_MONEY,
                        source=PaymentRecord.Source.MPESA,
                        payment_date=transaction_date.date(),
                        reference=receipt,
                        notes="M-Pesa STK Push",
                        recorded_by=None,
                    )
            except IntegrityError:
                attempt.result_code = result_code
                attempt.result_description = "M-Pesa receipt is already linked to another payment record."
                attempt.save(update_fields=("result_code", "result_description", "updated_at"))
                return Response({"ResultCode": 0, "ResultDesc": "Accepted"})
            attempt.status = MpesaPaymentAttempt.Status.SUCCEEDED
            attempt.result_code = result_code
            attempt.result_description = result_desc
            attempt.receipt_number = receipt
            attempt.transaction_date = transaction_date
            attempt.payment_record = record
            attempt.save(update_fields=("status", "result_code", "result_description", "receipt_number", "transaction_date", "payment_record", "updated_at"))
            record_domain_event(
                actor=None, organization=attempt.tenancy.unit.property.organization, obj=record,
                action="payment.mpesa_confirmed", request=request,
                changes={"amount": str(record.amount), "currency": record.currency, "receipt": receipt},
            )
        return Response({"ResultCode": 0, "ResultDesc": "Accepted"})
