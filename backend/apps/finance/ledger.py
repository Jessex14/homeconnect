import calendar
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum

from .models import PaymentAllocation, PaymentAllocationReversal, PaymentRecord, RentAdjustment, RentObligation

ZERO = Decimal("0.00")


def effective_tenancy_end(tenancy):
    dates = [value for value in (tenancy.end_date, tenancy.actual_end_date) if value]
    return min(dates) if dates else None


def _next_month(month):
    return date(month.year + (month.month == 12), 1 if month.month == 12 else month.month + 1, 1)


def active_payment_allocations(payment):
    return PaymentAllocation.objects.filter(payment=payment, reversal__isnull=True, payment__reversal__isnull=True)


def active_obligation_allocations(obligation):
    return PaymentAllocation.objects.filter(obligation=obligation, reversal__isnull=True, payment__reversal__isnull=True)


def allocated_to_payment(payment):
    return active_payment_allocations(payment).aggregate(total=Sum("amount"))["total"] or ZERO


def allocated_to_obligation(obligation):
    return active_obligation_allocations(obligation).aggregate(total=Sum("amount"))["total"] or ZERO


def adjusted_obligation_amount(obligation):
    adjustments = obligation.adjustments.aggregate(total=Sum("amount"))["total"] or ZERO
    return obligation.amount + adjustments


def payment_available_amount(payment):
    if hasattr(payment, "reversal"):
        return ZERO
    return payment.amount - allocated_to_payment(payment)


def obligation_balance(obligation):
    required = adjusted_obligation_amount(obligation)
    paid = allocated_to_obligation(obligation)
    return {"required": required, "allocated": paid, "outstanding": required - paid}


@transaction.atomic
def generate_month_obligation(*, tenancy, month, amount, actor):
    """Create an idempotent monthly charge. Partial months require a landlord amount."""
    month = date(month.year, month.month, 1)
    tenancy = type(tenancy).objects.select_for_update().get(pk=tenancy.pk)
    if tenancy.status not in {"active", "scheduled_to_end", "ended"}:
        raise ValidationError("Rent obligations can only be created for an accepted tenancy.")

    month_end = _next_month(month)
    tenancy_end = effective_tenancy_end(tenancy)
    occupied_from = max(month, tenancy.start_date)
    occupied_until = min(month_end, tenancy_end) if tenancy_end else month_end
    if occupied_from >= occupied_until:
        raise ValidationError("This month falls outside the tenancy dates.")

    is_partial = occupied_from != month or occupied_until != month_end
    if is_partial and amount is None:
        raise ValidationError("Enter the rent amount for this partial month.")
    if not is_partial:
        if amount is not None and amount != tenancy.agreed_rent:
            raise ValidationError("A full month's obligation must match the agreed monthly rent. Use an adjustment for an approved change.")
        amount = tenancy.agreed_rent
    if amount is None or amount <= ZERO:
        raise ValidationError("The rent amount must be greater than zero.")

    last_occupied = occupied_until - timedelta(days=1)
    due_day = min(tenancy.due_day, calendar.monthrange(month.year, month.month)[1])
    due_date = date(month.year, month.month, due_day)
    due_date = min(max(due_date, occupied_from), last_occupied)

    existing = RentObligation.objects.filter(tenancy=tenancy, period_month=month).first()
    if existing:
        if existing.amount != amount:
            raise ValidationError("A rent obligation already exists for this month with a different amount.")
        return existing, False, is_partial
    try:
        with transaction.atomic():
            obligation = RentObligation.objects.create(
                tenancy=tenancy, period_month=month, due_date=due_date,
                amount=amount, currency=tenancy.currency, created_by=actor,
            )
    except IntegrityError:
        obligation = RentObligation.objects.get(tenancy=tenancy, period_month=month)
        if obligation.amount != amount:
            raise ValidationError("A rent obligation already exists for this month with a different amount.")
        return obligation, False, is_partial
    return obligation, True, is_partial


@transaction.atomic
def add_rent_adjustment(*, obligation, amount, reason, actor):
    obligation = RentObligation.objects.select_for_update().get(pk=obligation.pk)
    new_required = adjusted_obligation_amount(obligation) + amount
    allocated = allocated_to_obligation(obligation)
    if new_required <= ZERO:
        raise ValidationError("An adjustment cannot reduce the rent charge to zero or less.")
    if new_required < allocated:
        raise ValidationError("The adjustment cannot reduce the charge below the amount already allocated.")
    adjustment = RentAdjustment.objects.create(obligation=obligation, amount=amount, reason=reason, created_by=actor)
    return adjustment


@transaction.atomic
def allocate_payment(*, payment, obligation, amount, idempotency_key, actor):
    # Lock the payment first so concurrent allocations cannot spend the same amount twice.
    payment = PaymentRecord.objects.select_for_update().get(pk=payment.pk)
    obligation = RentObligation.objects.select_for_update().get(pk=obligation.pk)
    if payment.tenancy_id != obligation.tenancy_id:
        raise ValidationError("A payment can only be allocated to a rent obligation for the same tenancy.")
    existing = PaymentAllocation.objects.filter(payment=payment, idempotency_key=idempotency_key).first()
    if existing:
        if existing.obligation_id != obligation.pk or existing.amount != amount:
            raise ValidationError("This request key was already used for a different allocation.")
        return existing, False
    if hasattr(payment, "reversal"):
        raise ValidationError("A reversed payment cannot be allocated.")
    available = payment.amount - allocated_to_payment(payment)
    outstanding = adjusted_obligation_amount(obligation) - allocated_to_obligation(obligation)
    if amount > available:
        raise ValidationError(f"Only {available} remains unallocated for this payment.")
    if amount > outstanding:
        raise ValidationError(f"Only {outstanding} remains outstanding for this rent charge. Leave any excess unallocated.")
    try:
        with transaction.atomic():
            allocation = PaymentAllocation.objects.create(
                payment=payment, obligation=obligation, idempotency_key=idempotency_key,
                amount=amount, created_by=actor,
            )
    except IntegrityError:
        allocation = PaymentAllocation.objects.get(payment=payment, idempotency_key=idempotency_key)
        if allocation.obligation_id != obligation.pk or allocation.amount != amount:
            raise ValidationError("This request key was already used for a different allocation.")
        return allocation, False
    return allocation, True


@transaction.atomic
def reverse_allocation(*, allocation, reason, actor):
    allocation = PaymentAllocation.objects.select_for_update().select_related("obligation").get(pk=allocation.pk)
    if hasattr(allocation, "reversal"):
        raise ValidationError("This allocation has already been reversed.")
    reversal = PaymentAllocationReversal.objects.create(allocation=allocation, reason=reason, reversed_by=actor)
    return allocation, reversal
