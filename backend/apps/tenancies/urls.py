from django.urls import path

from .views import (
    InvitationAcceptView,
    InvitationCancelView,
    InvitationDeclineView,
    InvitationResendView,
    InvitationReviewView,
    TenancyActivateView,
    TenancyDetailView,
    TenancyEndView,
    TenancyListView,
    UnitInvitationCreateView,
)

urlpatterns = [
    path("units/<uuid:unit_id>/invitations", UnitInvitationCreateView.as_view(), name="unit-invitation-create"),
    path("invitations/review", InvitationReviewView.as_view(), name="invitation-review"),
    path("invitations/accept", InvitationAcceptView.as_view(), name="invitation-accept"),
    path("invitations/decline", InvitationDeclineView.as_view(), name="invitation-decline"),
    path("invitations/<uuid:invitation_id>/resend", InvitationResendView.as_view(), name="invitation-resend"),
    path("invitations/<uuid:invitation_id>/cancel", InvitationCancelView.as_view(), name="invitation-cancel"),
    path("tenancies", TenancyListView.as_view(), name="tenancy-list"),
    path("tenancies/<uuid:tenancy_id>", TenancyDetailView.as_view(), name="tenancy-detail"),
    path("tenancies/<uuid:tenancy_id>/activate", TenancyActivateView.as_view(), name="tenancy-activate"),
    path("tenancies/<uuid:tenancy_id>/end", TenancyEndView.as_view(), name="tenancy-end"),
]
