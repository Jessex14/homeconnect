from django.urls import path

from .views import (
    MpesaCallbackView, MpesaPaymentStatusView, OrganizationMpesaSettingsView,
    PaymentAllocationReverseView, PaymentReceiptView, PaymentRecordReverseView, RentAdjustmentView,
    RentAllocationView, TenancyMpesaPaymentView, TenancyPaymentRecordsView,
    TenancyRentObligationsView,
)

urlpatterns = [
    path("tenancies/<uuid:tenancy_id>/payments", TenancyPaymentRecordsView.as_view(), name="tenancy-payment-records"),
    path("payments/<uuid:payment_id>/receipt", PaymentReceiptView.as_view(), name="payment-receipt"),
    path("payments/<uuid:payment_id>/reverse", PaymentRecordReverseView.as_view(), name="payment-record-reverse"),
    path("tenancies/<uuid:tenancy_id>/rent-obligations", TenancyRentObligationsView.as_view(), name="tenancy-rent-obligations"),
    path("rent-obligations/<uuid:obligation_id>/adjustments", RentAdjustmentView.as_view(), name="rent-obligation-adjustment"),
    path("rent-obligations/<uuid:obligation_id>/allocations", RentAllocationView.as_view(), name="rent-obligation-allocation"),
    path("payment-allocations/<uuid:allocation_id>/reverse", PaymentAllocationReverseView.as_view(), name="payment-allocation-reverse"),
    path("organizations/<uuid:organization_id>/mpesa", OrganizationMpesaSettingsView.as_view(), name="organization-mpesa-settings"),
    path("tenancies/<uuid:tenancy_id>/mpesa-payments", TenancyMpesaPaymentView.as_view(), name="tenancy-mpesa-payment"),
    path("mpesa-payments/<uuid:attempt_id>", MpesaPaymentStatusView.as_view(), name="mpesa-payment-status"),
    path("payment-callbacks/mpesa/<uuid:callback_token>", MpesaCallbackView.as_view(), name="mpesa-payment-callback"),
]
