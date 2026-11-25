"""ws/admin/sessions/?ticket=...: live updates for the administrator's session monitor (V2-047, BE-01)."""

from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.contrib.auth import get_user_model

from apps.dashboards.live_sessions import ADMIN_GROUP
from apps.realtime.consumers import CLOSE_BAD_TICKET, CLOSE_NOT_ALLOWED
from apps.realtime.tickets import user_from_ticket

User = get_user_model()


class AdminLiveSessionsConsumer(AsyncJsonWebsocketConsumer):
    """Sends {"type": "live_session.updated", "session": <row>} whenever a live call changes.

    Only system administrators get in. The row is the same metadata-only row the list endpoint returns.
    """

    group = None

    async def connect(self):
        ticket = parse_qs(self.scope["query_string"].decode()).get("ticket", [""])[0]

        # Accept first so the app receives the close code instead of a failed handshake.
        await self.accept()

        user = await database_sync_to_async(user_from_ticket)(ticket)
        if user is None:
            await self.close(code=CLOSE_BAD_TICKET)
            return
        if user.role != User.Role.ADMIN:
            await self.close(code=CLOSE_NOT_ALLOWED)
            return

        self.group = ADMIN_GROUP
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.send_json({"type": "live_sessions.ready"})

    async def disconnect(self, code):
        if self.group:
            await self.channel_layer.group_discard(self.group, self.channel_name)

    async def receive_json(self, content, **kwargs):
        """The page only listens; anything it sends is ignored."""

    async def live_event(self, event):
        await self.send_json(event["message"])
