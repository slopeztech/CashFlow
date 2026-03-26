from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from core.ws_events import push_live_update
from customers.models import BalanceRequest, MonthlyFeeSettings, StoreUserProfile


@receiver(post_save, sender=BalanceRequest)
def balance_request_live_updates(sender, instance, created, **kwargs):
    transaction.on_commit(
        lambda: push_live_update(
            event='balance_request_changed',
            include_admin=True,
            user_ids=[instance.user_id],
        )
    )


@receiver(post_save, sender=StoreUserProfile)
def monthly_profile_live_updates(sender, instance, created, **kwargs):
    user_ids = [instance.user_id] if instance.user_id else None
    transaction.on_commit(
        lambda: push_live_update(
            event='monthly_fee_profile_changed',
            include_admin=True,
            user_ids=user_ids,
        )
    )


@receiver(post_save, sender=MonthlyFeeSettings)
def monthly_settings_live_updates(sender, instance, created, **kwargs):
    transaction.on_commit(
        lambda: push_live_update(
            event='monthly_fee_settings_changed',
            include_admin=True,
            include_all_users=True,
        )
    )
