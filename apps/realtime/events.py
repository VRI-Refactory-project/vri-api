"""Sending to the people connected to a session (V2-011). See docs/design/V2-011-realtime.md."""

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction


def group_name(session_id):
    return f"session-{session_id}"


def send_to_session(session_id, event_type, data):
    """Send {"type": event_type, "session": session_id, **data} to everyone on the session's WebSocket.

    It goes out once the current database transaction commits, so nobody hears about a change that is rolled
    back. Call it from normal (sync) code such as services and views.
    """
    message = {"type": event_type, "session": session_id, **data}

    def send():
        async_to_sync(get_channel_layer().group_send)(
            group_name(session_id), {"type": "session.event", "message": message}
        )

    transaction.on_commit(send)
