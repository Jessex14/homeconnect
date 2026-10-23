import uuid
from unittest.mock import patch

from django.test import override_settings
from django.test import TestCase
from cryptography.fernet import Fernet
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.organizations.models import Organization, OrganizationMembership, Property
from apps.properties.models import Unit
from apps.tenancies.models import Tenancy

from .models import MpesaMerchantAccount, MpesaPaymentAttempt, PaymentAllocation, PaymentRecord, PaymentReversal, RentAdjustment, RentObligation
from .mpesa import decrypt_secret, encrypt_secret

TEST_MPESA_ENCRYPTION_KEY = Fernet.generate_key().decode("ascii")


class RentLedgerApiTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("ledger-owner@example.test", "Strong-pass-4321")
        self.tenant = User.objects.create_user("ledger-tenant@example.test", "Strong-pass-4321")
        self.other = User.objects.create_user("ledger-other@example.test", "Strong-pass-4321")
        organization = Organization.objects.create(name="Ledger portfolio")
        OrganizationMembership.objects.create(user=self.owner, organization=organization, role="owner")
        property_record = Property.objects.create(organization=organization, name="Ledger property")
        unit = Unit.objects.create(property=property_record, code="L-01")
        self.tenancy = Tenancy.objects.create(
            unit=unit, invited_email=self.tenant.email, tenant=self.tenant, status=Tenancy.Status.ACTIVE,
            start_date="2026-01-01", agreed_rent="25000.00", currency="KES", due_day=5, created_by=self.owner,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def create_charge(self, month, amount=None):
        payload = {"period_month": month}
        if amount is not None:
            payload["amount"] = amount
        return self.client.post(f"/api/v1/tenancies/{self.tenancy.pk}/rent-obligations", payload, format="json")

    def test_full_month_uses_agreed_rent_and_partial_month_requires_landlord_amount(self):
        full = self.create_charge("2026-02")
        self.assertEqual(full.status_code, 201)
        self.assertEqual(full.data["amount"], "25000.00")
        partial_tenancy = Tenancy.objects.create(
            unit=Unit.objects.create(property=self.tenancy.unit.property, code="L-02"),
            invited_email=self.tenant.email, tenant=self.tenant, status=Tenancy.Status.ACTIVE,
            start_date="2026-02-10", agreed_rent="25000.00", currency="KES", due_day=5, created_by=self.owner,
        )
        missing = self.client.post(f"/api/v1/tenancies/{partial_tenancy.pk}/rent-obligations", {"period_month": "2026-02"}, format="json")
        self.assertEqual(missing.status_code, 400)
        entered = self.client.post(f"/api/v1/tenancies/{partial_tenancy.pk}/rent-obligations", {"period_month": "2026-02", "amount": "18000.00"}, format="json")
        self.assertEqual(entered.status_code, 201)
        self.assertTrue(entered.data["is_partial"])
        self.assertEqual(entered.data["amount"], "18000.00")

    def test_payment_can_be_allocated_in_parts_and_excess_stays_unallocated(self):
        charge = self.create_charge("2026-02")
        payment = PaymentRecord.objects.create(
            tenancy=self.tenancy, idempotency_key=uuid.uuid4(), amount="30000.00", currency="KES",
            method="cash", payment_date="2026-02-05", recorded_by=self.owner,
        )
        endpoint = f"/api/v1/rent-obligations/{charge.data['id']}/allocations"
        payload = {"payment_id": str(payment.pk), "idempotency_key": str(uuid.uuid4()), "amount": "20000.00"}
        first = self.client.post(endpoint, payload, format="json")
        second = self.client.post(endpoint, payload, format="json")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data["id"], second.data["id"])
        self.assertEqual(PaymentAllocation.objects.count(), 1)
        refreshed_payment = self.client.get(f"/api/v1/tenancies/{self.tenancy.pk}/payments")
        self.assertEqual(str(refreshed_payment.data[0]["available_amount"]), "10000.00")
        too_much = {**payload, "idempotency_key": str(uuid.uuid4()), "amount": "6000.00"}
        self.assertEqual(self.client.post(endpoint, too_much, format="json").status_code, 400)

    def test_adjustments_are_audited_and_cannot_reduce_below_allocated_rent(self):
        charge = self.create_charge("2026-02")
        obligation = RentObligation.objects.get(pk=charge.data["id"])
        payment = PaymentRecord.objects.create(
            tenancy=self.tenancy, idempotency_key=uuid.uuid4(), amount="10000.00", currency="KES",
            method="bank_transfer", payment_date="2026-02-05", recorded_by=self.owner,
        )
        PaymentAllocation.objects.create(payment=payment, obligation=obligation, idempotency_key=uuid.uuid4(), amount="10000.00", created_by=self.owner)
        endpoint = f"/api/v1/rent-obligations/{obligation.pk}/adjustments"
        reduced_too_far = self.client.post(endpoint, {"amount": "-16000.00", "reason": "Correction"}, format="json")
        self.assertEqual(reduced_too_far.status_code, 400)
        accepted = self.client.post(endpoint, {"amount": "500.00", "reason": "Agreed utility adjustment"}, format="json")
        self.assertEqual(accepted.status_code, 201)
        self.assertEqual(RentAdjustment.objects.count(), 1)
        self.assertEqual(accepted.data["required_amount"], "25500.00")

    def test_tenant_can_view_balance_but_cannot_create_charge(self):
        self.create_charge("2026-02")
        self.client.force_authenticate(self.tenant)
        endpoint = f"/api/v1/tenancies/{self.tenancy.pk}/rent-obligations"
        self.assertEqual(self.client.get(endpoint).status_code, 200)
        self.assertEqual(self.client.post(endpoint, {"period_month": "2026-03"}, format="json").status_code, 404)


class PaymentRecordApiTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("finance-owner@example.test", "Strong-pass-4321")
        self.tenant = User.objects.create_user("finance-tenant@example.test", "Strong-pass-4321")
        self.other = User.objects.create_user("finance-other@example.test", "Strong-pass-4321")
        self.organization = Organization.objects.create(name="Finance portfolio")
        OrganizationMembership.objects.create(user=self.owner, organization=self.organization, role="owner")
        self.property = Property.objects.create(organization=self.organization, name="Test property")
        self.unit = Unit.objects.create(property=self.property, code="A-01")
        self.tenancy = Tenancy.objects.create(
            unit=self.unit, invited_email=self.tenant.email, tenant=self.tenant, status=Tenancy.Status.ACTIVE,
            start_date="2026-01-01", agreed_rent="25000.00", currency="KES", due_day=1, created_by=self.owner,
        )
        self.client = APIClient()

    def test_manager_can_record_idempotent_manual_payment(self):
        self.client.force_authenticate(self.owner)
        endpoint = f"/api/v1/tenancies/{self.tenancy.pk}/payments"
        payload = {
            "idempotency_key": str(uuid.uuid4()), "amount": "12000.00", "method": "mobile_money",
            "payment_date": "2026-02-01", "reference": "ABC123",
        }
        first = self.client.post(endpoint, payload, format="json")
        retry = self.client.post(endpoint, payload, format="json")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(first.data["id"], retry.data["id"])
        self.assertEqual(PaymentRecord.objects.count(), 1)

    def test_tenant_can_view_own_records_but_cannot_record_payment(self):
        PaymentRecord.objects.create(
            tenancy=self.tenancy, idempotency_key=uuid.uuid4(), amount="500.00", currency="KES",
            method="cash", payment_date="2026-02-01", recorded_by=self.owner,
        )
        self.client.force_authenticate(self.tenant)
        response = self.client.get(f"/api/v1/tenancies/{self.tenancy.pk}/payments")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]["record_type"], "Manual record — not provider-confirmed")
        rejected = self.client.post(f"/api/v1/tenancies/{self.tenancy.pk}/payments", {}, format="json")
        self.assertEqual(rejected.status_code, 404)

    def test_unrelated_user_cannot_view_or_record(self):
        self.client.force_authenticate(self.other)
        endpoint = f"/api/v1/tenancies/{self.tenancy.pk}/payments"
        self.assertEqual(self.client.get(endpoint).status_code, 404)
        self.assertEqual(self.client.post(endpoint, {}, format="json").status_code, 404)

    def test_manager_can_reverse_only_with_reason_and_history_is_preserved(self):
        record = PaymentRecord.objects.create(
            tenancy=self.tenancy, idempotency_key=uuid.uuid4(), amount="500.00", currency="KES",
            method="cash", payment_date="2026-02-01", recorded_by=self.owner,
        )
        self.client.force_authenticate(self.owner)
        endpoint = f"/api/v1/payments/{record.pk}/reverse"
        self.assertEqual(self.client.post(endpoint, {"reason": ""}, format="json").status_code, 400)
        self.assertEqual(self.client.post(endpoint, {"reason": "Entered in error"}, format="json").status_code, 200)
        self.assertEqual(PaymentRecord.objects.count(), 1)
        self.assertEqual(PaymentReversal.objects.get(payment=record).reason, "Entered in error")
        self.assertEqual(self.client.post(endpoint, {"reason": "Again"}, format="json").status_code, 409)

    def test_payment_record_cannot_be_overwritten_or_deleted(self):
        record = PaymentRecord.objects.create(
            tenancy=self.tenancy, idempotency_key=uuid.uuid4(), amount="500.00", currency="KES",
            method="cash", payment_date="2026-02-01", recorded_by=self.owner,
        )
        record.amount = "1.00"
        with self.assertRaises(ValueError):
            record.save()
        with self.assertRaises(ValueError):
            record.delete()

    def test_payment_receipt_is_scoped_and_labels_manual_records_unconfirmed(self):
        record = PaymentRecord.objects.create(
            tenancy=self.tenancy, idempotency_key=uuid.uuid4(), amount="1250.00", currency="KES",
            method="cash", payment_date="2026-02-03", reference="CASH-01", recorded_by=self.owner,
        )
        endpoint = f"/api/v1/payments/{record.pk}/receipt"
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(endpoint).status_code, 404)
        self.client.force_authenticate(self.tenant)
        tenant_receipt = self.client.get(endpoint)
        self.assertEqual(tenant_receipt.status_code, 200)
        self.assertContains(tenant_receipt, "MANUAL RECORD")
        self.assertContains(tenant_receipt, "not independently verified")
        self.client.force_authenticate(self.owner)
        self.assertContains(self.client.get(endpoint), "HC-")


class MpesaPaymentApiTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("mpesa-owner@example.test", "Strong-pass-4321")
        self.tenant = User.objects.create_user("mpesa-tenant@example.test", "Strong-pass-4321")
        self.other = User.objects.create_user("mpesa-other@example.test", "Strong-pass-4321")
        self.organization = Organization.objects.create(name="M-Pesa portfolio")
        OrganizationMembership.objects.create(user=self.owner, organization=self.organization, role="owner")
        self.property = Property.objects.create(organization=self.organization, name="M-Pesa property")
        self.unit = Unit.objects.create(property=self.property, code="M-01")
        self.tenancy = Tenancy.objects.create(
            unit=self.unit, invited_email=self.tenant.email, tenant=self.tenant, status=Tenancy.Status.ACTIVE,
            start_date="2026-01-01", agreed_rent="25000.00", currency="KES", due_day=1, created_by=self.owner,
        )
        self.client = APIClient()

    @override_settings(MPESA_CALLBACK_BASE_URL="https://homeconnect.example", MPESA_ALLOW_PRODUCTION=False, MPESA_CREDENTIALS_ENCRYPTION_KEY=TEST_MPESA_ENCRYPTION_KEY)
    def test_owner_can_save_encrypted_sandbox_credentials(self):
        self.client.force_authenticate(self.owner)
        endpoint = f"/api/v1/organizations/{self.organization.pk}/mpesa"
        payload = {
            "environment": "sandbox", "merchant_type": "paybill", "business_shortcode": "174379", "shortcode": "174379",
            "consumer_key": "sandbox-key", "consumer_secret": "sandbox-secret", "passkey": "sandbox-passkey",
        }
        saved = self.client.put(endpoint, payload, format="json")
        self.assertEqual(saved.status_code, 200)
        account = MpesaMerchantAccount.objects.get(organization=self.organization)
        self.assertNotEqual(account.consumer_secret_encrypted, "sandbox-secret")
        self.assertEqual(decrypt_secret(account.consumer_secret_encrypted), "sandbox-secret")
        self.assertNotIn("consumer_secret", saved.data)
        self.assertTrue(saved.data["callback_ready"])

    @override_settings(MPESA_CALLBACK_BASE_URL="https://homeconnect.example", MPESA_CREDENTIALS_ENCRYPTION_KEY=TEST_MPESA_ENCRYPTION_KEY)
    @patch("apps.finance.views.initiate_stk_push")
    def test_tenant_can_request_idempotent_stk_push(self, initiate):
        initiate.return_value = {"ResponseCode": "0", "CheckoutRequestID": "checkout-1", "MerchantRequestID": "merchant-1", "CustomerMessage": "Prompt sent."}
        MpesaMerchantAccount.objects.create(
            organization=self.organization, environment="sandbox", merchant_type="paybill", business_shortcode="174379", shortcode="174379",
            consumer_key_encrypted=encrypt_secret("key"), consumer_secret_encrypted=encrypt_secret("secret"), passkey_encrypted=encrypt_secret("passkey"),
        )
        self.client.force_authenticate(self.tenant)
        endpoint = f"/api/v1/tenancies/{self.tenancy.pk}/mpesa-payments"
        payload = {"idempotency_key": str(uuid.uuid4()), "amount": 25000, "phone_number": "0712 345 678"}
        first = self.client.post(endpoint, payload, format="json")
        retry = self.client.post(endpoint, payload, format="json")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(first.data["id"], retry.data["id"])
        self.assertEqual(MpesaPaymentAttempt.objects.count(), 1)
        self.assertEqual(MpesaPaymentAttempt.objects.get().phone_number, "254712345678")
        initiate.assert_called_once()

    def test_callback_records_only_matching_success_once(self):
        attempt = MpesaPaymentAttempt.objects.create(
            tenancy=self.tenancy, idempotency_key=uuid.uuid4(), amount=25000, phone_number="254712345678",
            status=MpesaPaymentAttempt.Status.PROCESSING, checkout_request_id="checkout-1", merchant_request_id="merchant-1",
        )
        endpoint = f"/api/v1/payment-callbacks/mpesa/{attempt.callback_token}"
        callback = {"Body": {"stkCallback": {
            "MerchantRequestID": "merchant-1", "CheckoutRequestID": "checkout-1", "ResultCode": 0,
            "ResultDesc": "The service request is processed successfully.",
            "CallbackMetadata": {"Item": [
                {"Name": "Amount", "Value": 25000.0}, {"Name": "MpesaReceiptNumber", "Value": "QAB1234567"},
                {"Name": "TransactionDate", "Value": 20261020121530}, {"Name": "PhoneNumber", "Value": 254712345678},
            ]},
        }}}
        first = self.client.post(endpoint, callback, format="json")
        retry = self.client.post(endpoint, callback, format="json")
        attempt.refresh_from_db()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(attempt.status, MpesaPaymentAttempt.Status.SUCCEEDED)
        self.assertEqual(PaymentRecord.objects.count(), 1)
        record = PaymentRecord.objects.get()
        self.assertEqual(record.source, PaymentRecord.Source.MPESA)
        self.assertIsNone(record.recorded_by)
        self.assertEqual(record.reference, "QAB1234567")
        self.client.force_authenticate(self.tenant)
        receipt = self.client.get(f"/api/v1/payments/{record.pk}/receipt")
        self.assertContains(receipt, "M-PESA CONFIRMED")
        self.assertContains(receipt, "QAB1234567")

    def test_callback_amount_mismatch_does_not_create_payment_record(self):
        attempt = MpesaPaymentAttempt.objects.create(
            tenancy=self.tenancy, idempotency_key=uuid.uuid4(), amount=25000, phone_number="254712345678",
            status=MpesaPaymentAttempt.Status.PROCESSING, checkout_request_id="checkout-2", merchant_request_id="merchant-2",
        )
        payload = {"Body": {"stkCallback": {
            "MerchantRequestID": "merchant-2", "CheckoutRequestID": "checkout-2", "ResultCode": 0,
            "CallbackMetadata": {"Item": [
                {"Name": "Amount", "Value": 1.5}, {"Name": "MpesaReceiptNumber", "Value": "QAB1234568"},
                {"Name": "TransactionDate", "Value": 20261020121530}, {"Name": "PhoneNumber", "Value": 254712345678},
            ]},
        }}}
        response = self.client.post(f"/api/v1/payment-callbacks/mpesa/{attempt.callback_token}", payload, format="json")
        attempt.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(attempt.status, MpesaPaymentAttempt.Status.PROCESSING)
        self.assertEqual(PaymentRecord.objects.count(), 0)

    def test_confirmed_mpesa_record_cannot_be_locally_reversed(self):
        record = PaymentRecord.objects.create(
            tenancy=self.tenancy, idempotency_key=uuid.uuid4(), amount="25000.00", currency="KES",
            method="mobile_money", source=PaymentRecord.Source.MPESA, payment_date="2026-02-01",
            reference="QAB1234569", recorded_by=None,
        )
        self.client.force_authenticate(self.owner)
        response = self.client.post(f"/api/v1/payments/{record.pk}/reverse", {"reason": "Refund"}, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertFalse(PaymentReversal.objects.filter(payment=record).exists())

    def test_non_owner_cannot_view_landlord_daraja_settings(self):
        self.client.force_authenticate(self.other)
        response = self.client.get(f"/api/v1/organizations/{self.organization.pk}/mpesa")
        self.assertEqual(response.status_code, 404)
