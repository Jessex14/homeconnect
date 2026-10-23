import uuid

from django.conf import settings
from django.db import models
from django.core.serializers.json import DjangoJSONEncoder


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError("Security events are append-only")

    def delete(self):
        raise ValueError("Security events are append-only")


class SecurityEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)
    event_type = models.CharField(max_length=80)
    outcome = models.CharField(max_length=24, default="success")
    request_id = models.CharField(max_length=64, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-occurred_at",)
        indexes = [models.Index(fields=("event_type", "occurred_at")), models.Index(fields=("actor", "occurred_at"))]

    objects = AppendOnlyQuerySet.as_manager()

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValueError("Security events are append-only")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Security events are append-only")


class DomainAuditQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError("Domain audit events are append-only")

    def delete(self):
        raise ValueError("Domain audit events are append-only")


class DomainAuditEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)
    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT, related_name="audit_events")
    object_type = models.CharField(max_length=80)
    object_id = models.CharField(max_length=64)
    action = models.CharField(max_length=80)
    reason = models.CharField(max_length=500, blank=True)
    changes = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    request_id = models.CharField(max_length=64, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True)

    objects = DomainAuditQuerySet.as_manager()

    class Meta:
        ordering = ("occurred_at",)
        indexes = [models.Index(fields=("organization", "occurred_at")), models.Index(fields=("object_type", "object_id"))]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValueError("Domain audit events are append-only")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Domain audit events are append-only")
