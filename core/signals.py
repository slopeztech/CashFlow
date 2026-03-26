from django.db.models.signals import post_delete, post_save
from django.db import transaction
from django.dispatch import receiver

from core.models import Event, EventComment, EventRegistration, Notice
from core.ws_events import push_live_update


@receiver(post_save, sender=Notice)
def notice_live_updates(sender, instance, created, **kwargs):
    transaction.on_commit(
        lambda: push_live_update(event='notice_changed', include_admin=True, include_all_users=True)
    )


@receiver(post_save, sender=Event)
def event_live_updates(sender, instance, created, **kwargs):
    transaction.on_commit(
        lambda: push_live_update(event='event_changed', include_admin=True, include_all_users=True)
    )


@receiver(post_save, sender=EventRegistration)
def event_registration_live_updates(sender, instance, created, **kwargs):
    transaction.on_commit(
        lambda: push_live_update(
            event='event_registration_changed',
            include_admin=True,
            user_ids=[instance.user_id],
        )
    )


@receiver(post_save, sender=EventComment)
def event_comment_live_updates(sender, instance, created, **kwargs):
    transaction.on_commit(
        lambda: push_live_update(event='event_comment_changed', include_admin=True, include_all_users=True)
    )


@receiver(post_delete, sender=EventComment)
def event_comment_delete_live_updates(sender, instance, **kwargs):
    transaction.on_commit(
        lambda: push_live_update(event='event_comment_changed', include_admin=True, include_all_users=True)
    )


@receiver(post_delete, sender=EventRegistration)
def event_registration_delete_live_updates(sender, instance, **kwargs):
    transaction.on_commit(
        lambda: push_live_update(
            event='event_registration_changed',
            include_admin=True,
            user_ids=[instance.user_id],
        )
    )
