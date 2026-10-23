from django.urls import path

from .views import OrganizationDetailView, OrganizationListCreateView, OrganizationMemberDeactivateView, OrganizationMembersView, PropertyScopeDetailView, PropertyScopeListView

urlpatterns = [
    path("organizations", OrganizationListCreateView.as_view(), name="organization-list-create"),
    path("organizations/<uuid:id>", OrganizationDetailView.as_view(), name="organization-detail"),
    path("organizations/<uuid:organization_id>/members", OrganizationMembersView.as_view(), name="organization-members"),
    path("organizations/<uuid:organization_id>/members/<int:membership_id>", OrganizationMemberDeactivateView.as_view(), name="organization-member-deactivate"),
    path("property-scopes", PropertyScopeListView.as_view(), name="property-scope-list"),
    path("property-scopes/<uuid:id>", PropertyScopeDetailView.as_view(), name="property-scope-detail"),
]
