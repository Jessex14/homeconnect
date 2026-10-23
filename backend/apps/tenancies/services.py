import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from apps.audit.services import record_domain_event
from apps.properties.models import Unit

from .models import TenantInvitation, Tenancy, TenancyTermsVersion

LIVE_STATUSES = (
    Tenancy.Status.INVITED,
    Tenancy.Status.ACCEPTED_PENDING_START,
    Tenancy.Status.ACTIVE,
    Tenancy.Status.SCHEDULED_TO_END,
)
BLOCKING_STATUSES = LIVE_STATUSES + (Tenancy.Status.ENDED,)


def token_digest(raw_token):
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def interval_overlaps(start_a, end_a, start_b, end_b):
    return (end_b is None or start_a < end_b) and (end_a is None or start_b < end_a)


def tenancy_conflict(unit, start_date, end_date, exclude_tenancy_id=None):
    records = Tenancy.objects.filter(unit=unit, status__in=BLOCKING_STATUSES)
    if exclude_tenancy_id:
        records = records.exclude(pk=exclude_tenancy_id)
    for existing in records:
        existing_end = existing.actual_end_date if existing.status == Tenancy.Status.ENDED else existing.end_date
        if interval_overlaps(start_date, end_date, existing.start_date, existing_end):
            return existing
    return None


def expire_stale_invites(unit, request=None):
    now = timezone.now()
    stale = TenantInvitation.objects.select_for_update().filter(
        unit=unit,
        status=TenantInvitation.Status.PENDING,
        expires_at__lte=now,
    ).select_related("tenancy")
    for invitation in stale:
        invitation.status = TenantInvitation.Status.EXPIRED
        invitation.save(update_fields=("status", "updated_at"))
        tenancy = invitation.tenancy
        tenancy.status = Tenancy.Status.EXPIRED
        tenancy.save(update_fields=("status", "updated_at"))
        record_domain_event(
            actor=None,
            organization=unit.property.organization,
            obj=tenancy,
            action="tenancy.invitation_expired",
            request=request,
            changes={"status": Tenancy.Status.EXPIRED},
        )


def refresh_unit_status(unit):
    if unit.status in (Unit.Status.MAINTENANCE, Unit.Status.INACTIVE, Unit.Status.DRAFT):
        return
    statuses = list(Tenancy.objects.filter(unit=unit, status__in=LIVE_STATUSES).values_list("status", flat=True))
    if any(value in (Tenancy.Status.ACTIVE, Tenancy.Status.SCHEDULED_TO_END) for value in statuses):
        unit.status = Unit.Status.OCCUPIED
    elif statuses:
        unit.status = Unit.Status.RESERVED
    else:
        unit.status = Unit.Status.AVAILABLE
    unit.save(update_fields=("status",))


def create_terms_version(tenancy, actor):
    return TenancyTermsVersion.objects.create(
        tenancy=tenancy,
        version=tenancy.terms_version,
        actor=actor,
        start_date=tenancy.start_date,
        end_date=tenancy.end_date,
        agreed_rent=tenancy.agreed_rent,
        currency=tenancy.currency,
        due_day=tenancy.due_day,
        deposit_amount=tenancy.deposit_amount,
        deposit_terms=tenancy.deposit_terms,
        agreement_reference=tenancy.agreement_reference,
    )


def deliver_invitation(invitation, raw_token):
    frontend_url = getattr(settings, "TENANT_INVITATION_FRONTEND_URL", "http://localhost:5173/tenant-invitation")
    link = f"{frontend_url}#invitation_id={invitation.pk}&token={raw_token}"
    body = (
        "You have been invited to review a proposed tenancy. Sign in with this email address, "
        "open the one-time link, review the terms, and accept only if they are correct.\n\n"
        f"{link}\n\nThis invitation expires at {invitation.expires_at.isoformat()}."
    )
    try:
        sent = send_mail(
            subject="Review your tenancy invitation",
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[invitation.email],
            fail_silently=False,
        )
    except Exception:
        sent = 0
    invitation.delivery_status = TenantInvitation.DeliveryStatus.SENT if sent == 1 else TenantInvitation.DeliveryStatus.FAILED
    invitation.save(update_fields=("delivery_status", "updated_at"))
    return sent == 1


def new_invitation_token():
    return secrets.token_urlsafe(32)


def invitation_expiry():
    hours = max(1, int(getattr(settings, "TENANT_INVITATION_TTL_HOURS", 72)))
    return timezone.now() + timedelta(hours=hours)
