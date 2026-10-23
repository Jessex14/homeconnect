from django.urls import path

from .views import (
    MaintenanceAssignView,
    MaintenanceAttachmentView,
    MaintenanceCommentListCreateView,
    MaintenanceTicketDetailView,
    MaintenanceTicketListCreateView,
    MaintenanceTransitionView,
)

urlpatterns = [
    path("maintenance-tickets", MaintenanceTicketListCreateView.as_view(), name="maintenance-tickets"),
    path("maintenance-attachments/<uuid:attachment_id>", MaintenanceAttachmentView.as_view(), name="maintenance-attachment"),
    path("maintenance-tickets/<uuid:ticket_id>", MaintenanceTicketDetailView.as_view(), name="maintenance-ticket-detail"),
    path("maintenance-tickets/<uuid:ticket_id>/assign", MaintenanceAssignView.as_view(), name="maintenance-ticket-assign"),
    path("maintenance-tickets/<uuid:ticket_id>/transition", MaintenanceTransitionView.as_view(), name="maintenance-ticket-transition"),
    path("maintenance-tickets/<uuid:ticket_id>/comments", MaintenanceCommentListCreateView.as_view(), name="maintenance-ticket-comments"),
]
