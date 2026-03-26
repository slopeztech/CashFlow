from django.utils import timezone


def push_live_update(*, event='changed', user_ids=None, include_admin=True, include_all_users=False):
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer
    except ImportError:
        return

    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    message = {
        'type': 'live.update',
        'event': event,
        'scope': event,
        'timestamp': timezone.now().isoformat(),
    }

    group_names = set()
    if include_admin:
        group_names.add('live_updates_admin')
    if include_all_users:
        group_names.add('live_updates_all_users')
    if user_ids:
        for user_id in user_ids:
            if user_id:
                group_names.add(f'live_updates_user_{user_id}')

    for group_name in group_names:
        async_to_sync(channel_layer.group_send)(group_name, message)
