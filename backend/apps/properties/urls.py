from django.urls import path

from .views import BuildingDetailView, OrganizationPropertyListCreateView, PropertyArchiveView, PropertyBuildingsView, PropertyDetailView, PropertyUnitsView, PublicRentalListingListView, RentalListingPhotoDetailView, UnitDetailView, UnitRentalListingPhotosView, UnitRentalListingView

urlpatterns = [
    path("organizations/<uuid:organization_id>/properties", OrganizationPropertyListCreateView.as_view(), name="organization-properties"),
    path("properties/<uuid:property_id>", PropertyDetailView.as_view(), name="property-detail"),
    path("properties/<uuid:property_id>/archive", PropertyArchiveView.as_view(), name="property-archive"),
    path("properties/<uuid:property_id>/buildings", PropertyBuildingsView.as_view(), name="property-buildings"),
    path("buildings/<uuid:building_id>", BuildingDetailView.as_view(), name="building-detail"),
    path("properties/<uuid:property_id>/units", PropertyUnitsView.as_view(), name="property-units"),
    path("units/<uuid:unit_id>", UnitDetailView.as_view(), name="unit-detail"),
    path("units/<uuid:unit_id>/listing", UnitRentalListingView.as_view(), name="unit-rental-listing"),
    path("units/<uuid:unit_id>/listing/photos", UnitRentalListingPhotosView.as_view(), name="unit-rental-listing-photos"),
    path("listing-photos/<uuid:photo_id>", RentalListingPhotoDetailView.as_view(), name="rental-listing-photo-detail"),
    path("public/listings", PublicRentalListingListView.as_view(), name="public-rental-listings"),
]
