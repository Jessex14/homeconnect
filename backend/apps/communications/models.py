import uuid

from django.conf import settings
from django.db import models


class NotificationIntent(models.Model):
    """Transactional notification outbox record; never sent without channel policy."""

    class Status(models.TextChoices):
        AWAITING_POLICY = "awaiting_policy", "Awaiting channel policy"
        QUEUED = "queued", "Queued"
        SENDING = "sending", "Sending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"
        SUPPRESSED = "suppressed", "Suppressed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="notification_intents")
    event_type = models.CharField(max_length=80)
    template_key = models.CharField(max_length=100)
    template_version = models.PositiveSmallIntegerField(default=1)
    object_type = models.CharField(max_length=80)
    object_id = models.CharField(max_length=80)
    # Channel stays blank until consent, preference, vendor, and retention policy are approved.
    channel = models.CharField(max_length=16, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.AWAITING_POLICY)
    dedupe_key = models.CharField(max_length=180, unique=True)
    payload = models.JSONField(default=dict, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    provider_message_id = models.CharField(max_length=180, blank=True)
    failure_code = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    available_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=("status", "available_at"))]
        ordering = ("created_at",)
