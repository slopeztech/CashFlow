from django.conf import settings
from django.db.models import Exists, OuterRef

from customers.models import BalanceRequest
from customers.models import MonthlyFeeSettings
from customers.models import StoreUserProfile
from customers.services import months_due_for_profile
from core.models import EventComment
from core.models import SystemSettings
from inventory.models import ProductReview
from sales.models import Order


def admin_pending_counts(request):
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated or not user.is_staff:
        return {}

    pending_event_comments_count = (
        EventComment.objects.filter(
            parent__isnull=True,
            user__is_staff=False,
            is_ignored_by_admin=False,
        )
        .annotate(
            has_staff_reply=Exists(
                EventComment.objects.filter(parent_id=OuterRef('pk'), user__is_staff=True)
            )
        )
        .filter(has_staff_reply=False)
        .count()
    )

    settings_obj, _settings_created = MonthlyFeeSettings.objects.get_or_create(pk=1)
    pending_monthly_fee_late_count = 0
    if settings_obj.is_active and settings_obj.monthly_amount > 0:
        monthly_profiles = StoreUserProfile.objects.filter(user__is_staff=False, monthly_fee_enabled=True).only(
            'id',
            'monthly_fee_enabled',
            'monthly_fee_enabled_at',
            'monthly_fee_last_charged_month',
        )
        for profile in monthly_profiles:
            if months_due_for_profile(profile) > 0:
                pending_monthly_fee_late_count += 1

    return {
        'admin_pending_orders_count': Order.objects.filter(status=Order.Status.PENDING).count(),
        'admin_pending_reviews_count': ProductReview.objects.filter(is_approved=False).count(),
        'admin_pending_balance_requests_count': BalanceRequest.objects.filter(
            status=BalanceRequest.Status.PENDING
        ).count(),
        'admin_pending_event_comments_count': pending_event_comments_count,
        'admin_pending_monthly_fee_late_count': pending_monthly_fee_late_count,
    }


def current_user_profile(request):
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return {'current_user_profile': None}
    profile = StoreUserProfile.objects.filter(user=user).first()
    return {'current_user_profile': profile}


def ui_settings(request):
    settings_obj, _created = SystemSettings.objects.get_or_create(pk=1)
    return {
        'ui_settings': settings_obj,
        'live_updates_enabled': bool(getattr(settings, 'REALTIME_ENABLED', False)),
    }
