"""Pushing chat changes to people who have the chat socket open (ws/community/).

Each person has one group, so every device they have open hears about a change. Sending never breaks
the request that caused it: if the channel layer is down, the change is still saved and the app
catches up the next time it loads the conversation.
"""

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction

logger = logging.getLogger(__name__)


def user_group(user_id):
    return f"community-user-{user_id}"


def _send(user_id, message):
    def send():
        try:
            async_to_sync(get_channel_layer().group_send)(
                user_group(user_id), {"type": "community.event", "message": message}
            )
        except (
            Exception
        ):  # the change is saved; a missed push must not turn it into an error
            logger.exception("Could not push a chat event to user %s", user_id)

    # Only after the change is committed, so nobody hears about something that was rolled back.
    transaction.on_commit(send)


def _people(connection):
    return (connection.requester_id, connection.addressee_id)


def announce_message(connection, message):
    """A new message, to both people (each sees it from their own side: `sent_by_me`)."""
    from .serializers import MessageSerializer, message_data

    for user in (connection.requester, connection.addressee):
        _send(
            user.id,
            {
                "type": "community.message",
                "connection": connection.id,
                # Through the serializer so dates are text: the socket and Redis carry plain values only.
                "message": dict(MessageSerializer(message_data(message, user)).data),
            },
        )


def announce_deleted(connection, message_id):
    for user_id in _people(connection):
        _send(
            user_id,
            {
                "type": "community.message_deleted",
                "connection": connection.id,
                "message_id": message_id,
            },
        )


def announce_read(connection, reader, message_ids):
    """Tell the sender(s) which of their messages the reader just opened."""
    other = (
        connection.addressee_id
        if connection.requester_id == reader.id
        else connection.requester_id
    )
    _send(
        other,
        {
            "type": "community.read",
            "connection": connection.id,
            "message_ids": message_ids,
        },
    )
