from django.urls import path

from .views import RentQuestionView


urlpatterns = [
    path("tenancies/<uuid:tenancy_id>/rent-questions", RentQuestionView.as_view(), name="tenancy-rent-questions"),
]

