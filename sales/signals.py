from django.db.models.signals import post_save
from django.db import transaction
from django.dispatch import receiver

from core.ws_events import push_live_update
from sales.models import Order, Sale


@receiver(post_save, sender=Order)
def order_live_updates(sender, instance, created, **kwargs):
    transaction.on_commit(
        lambda: push_live_update(
            event='order_changed',
            include_admin=True,
            user_ids=[instance.created_by_id],
        )
    )


@receiver(post_save, sender=Sale)
def sale_live_updates(sender, instance, created, **kwargs):
    user_ids = [instance.customer_id] if instance.customer_id else None
    transaction.on_commit(
        lambda: push_live_update(
            event='sale_changed',
            include_admin=True,
            user_ids=user_ids,
        )
    )
