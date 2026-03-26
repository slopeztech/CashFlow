from channels.generic.websocket import AsyncJsonWebsocketConsumer


class LiveUpdatesConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope.get('user')
        if not user or not user.is_authenticated:
            await self.close(code=4401)
            return

        self.group_names = {'live_updates_all_users', f'live_updates_user_{user.id}'}
        if user.is_staff:
            self.group_names.add('live_updates_admin')

        for group_name in self.group_names:
            await self.channel_layer.group_add(group_name, self.channel_name)

        await self.accept()

    async def disconnect(self, close_code):
        for group_name in getattr(self, 'group_names', set()):
            await self.channel_layer.group_discard(group_name, self.channel_name)

    async def receive_json(self, content, **kwargs):
        # Client-to-server messages are not required for this channel.
        return

    async def live_update(self, event):
        await self.send_json(
            {
                'type': 'live_update',
                'event': event.get('event', 'changed'),
                'scope': event.get('scope', 'global'),
                'timestamp': event.get('timestamp'),
            }
        )
