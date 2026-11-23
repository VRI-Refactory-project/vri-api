"""The chat WebSocket: ws/community/?ticket=...

One socket per signed-in Deaf user, however many conversations they have. It only listens: messages are
still sent, deleted and marked read through the normal API, and the people involved hear about it here.
Events: community.message, community.message_deleted, community.read (see events.py).
"""

from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.contrib.auth import get_user_model

from apps.realtime.consumers import CLOSE_BAD_TICKET, CLOSE_NOT_ALLOWED
from apps.realtime.tickets import user_from_ticket

from .events import user_group


class CommunityConsumer(AsyncJsonWebsocketConsumer):
    group = None

    async def connect(self):
        ticket = parse_qs(self.scope["query_string"].decode()).get("ticket", [""])[0]

        # Accept first so the app receives the close code instead of a failed handshake.
        await self.accept()

        user = await database_sync_to_async(user_from_ticket)(ticket)
        if user is None:
            await self.close(code=CLOSE_BAD_TICKET)
            return
        if user.role != get_user_model().Role.DEAF_HOH:
            await self.close(code=CLOSE_NOT_ALLOWED)
            return

        self.group = user_group(user.id)
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.send_json({"type": "community.ready"})

    async def disconnect(self, code):
        if self.group:
            await self.channel_layer.group_discard(self.group, self.channel_name)

    async def community_event(self, event):
        await self.send_json(event["message"])
