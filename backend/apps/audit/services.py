from .models import DomainAuditEvent, SecurityEvent


def record_security_event(event_type, request, outcome="success", actor=None):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
    remote = forwarded or request.META.get("REMOTE_ADDR")
    # Trust forwarded addresses only when deployment config adds a trusted proxy boundary.
    if forwarded:
        remote = request.META.get("REMOTE_ADDR")
    return SecurityEvent.objects.create(
        actor=actor,
        event_type=event_type,
        outcome=outcome,
        request_id=request.headers.get("X-Request-ID", "")[:64],
        ip_address=remote or None,
    )


def record_domain_event(*, actor, organization, obj, action, request=None, reason="", changes=None):
    return DomainAuditEvent.objects.create(
        actor=actor,
        organization=organization,
        object_type=obj._meta.label_lower,
        object_id=str(obj.pk),
        action=action,
        reason=reason[:500],
        changes=changes or {},
        request_id=request.headers.get("X-Request-ID", "")[:64] if request else "",
    )
