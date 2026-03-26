from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from customers.models import BalanceLog, MonthlyFeeSettings, StoreUserProfile


def _month_start(value: date) -> date:
    return date(value.year, value.month, 1)


def _next_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def months_due_for_profile(profile: StoreUserProfile, as_of: date | None = None) -> int:
    if not profile.monthly_fee_enabled:
        return 0

    today = as_of or timezone.localdate()
    current_month = _month_start(today)

    if profile.monthly_fee_last_charged_month:
        due_start = _next_month(_month_start(profile.monthly_fee_last_charged_month))
    elif profile.monthly_fee_enabled_at:
        # First charge is at the start of the month after enabling monthly fee.
        due_start = _next_month(_month_start(profile.monthly_fee_enabled_at))
    else:
        # If enabled date is missing, avoid charging immediately in the current month.
        due_start = _next_month(current_month)

    if due_start > current_month:
        return 0

    return (current_month.year - due_start.year) * 12 + (current_month.month - due_start.month) + 1


def process_monthly_fee_for_user(user, as_of: date | None = None):
    if not user or not user.is_authenticated:
        return 0

    settings = MonthlyFeeSettings.objects.first()
    if not settings or not settings.is_active or settings.monthly_amount <= Decimal('0'):
        return 0

    profile, _ = StoreUserProfile.objects.get_or_create(user=user)
    if not profile.monthly_fee_enabled:
        return 0

    months_due = months_due_for_profile(profile, as_of=as_of)
    if months_due <= 0:
        return 0

    today = as_of or timezone.localdate()
    current_month = _month_start(today)

    with transaction.atomic():
        profile = StoreUserProfile.objects.select_for_update().get(pk=profile.pk)

        months_due = months_due_for_profile(profile, as_of=as_of)
        if months_due <= 0:
            return 0

        if profile.monthly_fee_last_charged_month:
            charge_month = _next_month(_month_start(profile.monthly_fee_last_charged_month))
        elif profile.monthly_fee_enabled_at:
            charge_month = _next_month(_month_start(profile.monthly_fee_enabled_at))
        else:
            charge_month = _next_month(current_month)

        charged = 0
        while charge_month <= current_month:
            balance_before = profile.current_balance
            profile.current_balance = balance_before - settings.monthly_amount
            profile.monthly_fee_last_charged_month = charge_month
            profile.save(update_fields=['current_balance', 'monthly_fee_last_charged_month', 'updated_at'])
            BalanceLog.objects.create(
                user=user,
                changed_by=None,
                source=BalanceLog.Source.MONTHLY_FEE,
                amount_delta=-settings.monthly_amount,
                balance_before=balance_before,
                balance_after=profile.current_balance,
                note=f'Monthly fee {charge_month.strftime("%Y-%m")}',
            )
            charged += 1
            charge_month = _next_month(charge_month)

    return charged
