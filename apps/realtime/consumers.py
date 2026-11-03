"""The WebSocket for one session (V2-011). See docs/design/V2-011-realtime.md."""

from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.db.models import Q

from apps.realtime.events import group_name
from apps.realtime.tickets import user_from_ticket
from apps.service_requests.models import ServiceRequest

# Close codes the apps act on: 4401 get a new ticket and reconnect, 4403 stop trying.
CLOSE_BAD_TICKET = 4401
CLOSE_NOT_ALLOWED = 4403


class SessionConsumer(AsyncJsonWebsocketConsumer):
    """ws/sessions/<id>/?ticket=...: status and call events for the people on one session."""

    group = None

    async def connect(self):
        session_id = self.scope["url_route"]["kwargs"]["session_id"]
        ticket = parse_qs(self.scope["query_string"].decode()).get("ticket", [""])[0]
        # Accept first, so the app receives the close code instead of a bare failed handshake.
        await self.accept()

        user = await database_sync_to_async(user_from_ticket)(ticket)
        if user is None:
            await self.close(code=CLOSE_BAD_TICKET)
            return

        # Join the group before reading the status, so no change can slip in between the two.
        self.group = group_name(session_id)
        await self.channel_layer.group_add(self.group, self.channel_name)
        session = await database_sync_to_async(_session_for)(user, session_id)
        if session is None:
            await self.close(code=CLOSE_NOT_ALLOWED)
            return
        await self.send_json(
            {"type": "session.status", "session": session.pk, "status": session.status}
        )

    async def disconnect(self, code):
        if self.group:
            await self.channel_layer.group_discard(self.group, self.channel_name)

    async def session_event(self, event):
        """A message from send_to_session, passed on to the app as it is."""
        await self.send_json(event["message"])


def _session_for(user, session_id):
    """The session if this user may follow it: the requester at any stage, the interpreter once accepted."""
    return ServiceRequest.objects.filter(
        Q(requester=user) | Q(interpreter__user=user, status=ServiceRequest.Status.ASSIGNED),
        pk=session_id,
    ).first()
