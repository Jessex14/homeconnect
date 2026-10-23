from .models import NotificationIntent


def enqueue_notification(*, recipient, event_type, template_key, object_type, object_id, dedupe_key, payload=None):
    """Record a minimal intent in the caller's transaction; dispatch is policy-gated."""
    intent, _ = NotificationIntent.objects.get_or_create(
        dedupe_key=dedupe_key,
        defaults={
            "recipient": recipient,
            "event_type": event_type,
            "template_key": template_key,
            "object_type": object_type,
            "object_id": str(object_id),
            "payload": payload or {},
        },
    )
    return intent
