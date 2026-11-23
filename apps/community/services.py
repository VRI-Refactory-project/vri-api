"""Rules for who can connect with whom. Views call these so the rules live in one place."""

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, OuterRef, Q, Subquery
from django.utils import timezone

from . import events
from .models import Connection, Message, MessageFile, MessageImage
from apps.notifications.models import Notification
from apps.notifications.services import create_notification

User = get_user_model()

NONE = "none"
REQUEST_SENT = "request_sent"
REQUEST_RECEIVED = "request_received"
CONNECTED = "connected"


class CannotConnect(Exception):
    """The request breaks a rule. The message is safe to show the user."""


def community_members():
    """Everyone who can be found and connected with: active Deaf users."""
    return User.objects.filter(role=User.Role.DEAF_HOH, is_active=True)


def between(a, b):
    """The connection record between two people, whoever asked, or None."""
    return Connection.objects.filter(
        Q(requester=a, addressee=b) | Q(requester=b, addressee=a)
    ).first()


def relationship_of(me, connection):
    """How `me` stands with the other person in this record, as the app shows it."""
    if connection is None:
        return NONE
    if connection.status == Connection.Status.ACCEPTED:
        return CONNECTED
    if connection.requester_id == me.id:
        # A declined request still reads as "sent", so nobody is told they were turned down.
        return REQUEST_SENT
    if connection.status == Connection.Status.PENDING:
        return REQUEST_RECEIVED
    return NONE


def relationships_for(me, people):
    """{user id: relationship} for a page of people, in one query."""
    ids = [p.id for p in people]
    records = Connection.objects.filter(
        Q(requester=me, addressee_id__in=ids) | Q(addressee=me, requester_id__in=ids)
    )
    result = {}
    for record in records:
        other = (
            record.addressee_id if record.requester_id == me.id else record.requester_id
        )
        result[other] = relationship_of(me, record)
    return result


def _create_request(requester, target):
    """Save the request and tell the person who was asked."""
    connection = Connection.objects.create(requester=requester, addressee=target)
    create_notification(
        user=target,
        title="New connection request",
        body=f"{requester.full_name} wants to connect with you.",
        category=Notification.Category.CONNECTION_REQUEST,
        data={
            "url": "/dashboard/deaf/messages/community",
            "connection_id": connection.id,
        },
    )
    return connection


def send_request(requester, target):
    """Ask `target` to connect. Returns (connection, accepted_straight_away)."""
    if target.id == requester.id:
        raise CannotConnect("You cannot connect with yourself.")
    existing = between(requester, target)
    if existing is None:
        return _create_request(requester, target), False
    if existing.status == Connection.Status.ACCEPTED:
        raise CannotConnect("You are already connected.")
    if existing.requester_id == requester.id:
        raise CannotConnect("You have already sent this person a request.")
    if existing.status == Connection.Status.PENDING:
        # They asked first, so asking back means both of you want it: connect right away.
        accept(existing)
        return existing, True
    # They declined a request from this person earlier; a fresh request from the other side is fine.
    existing.delete()
    return _create_request(requester, target), False


def clear_request_notification(connection):
    """Remove the "wants to connect" notification once the request has an answer (or is withdrawn)."""
    Notification.objects.filter(
        user_id=connection.addressee_id,
        category=Notification.Category.CONNECTION_REQUEST,
        data__connection_id=connection.id,
    ).delete()


def withdraw(connection):
    """The person who asked takes the request back."""
    clear_request_notification(connection)
    connection.delete()


def accept(connection):
    clear_request_notification(connection)
    connection.status = Connection.Status.ACCEPTED
    connection.responded_at = timezone.now()
    connection.save(update_fields=["status", "responded_at"])
    create_notification(
        user=connection.requester,
        title="Connection accepted",
        body=f"{connection.addressee.full_name} accepted your request.",
        category=Notification.Category.CONNECTION_ACCEPTED,
        data={
            "url": f"/dashboard/deaf/messages?c={connection.id}",
            "connection_id": connection.id,
        },
    )


def decline(connection):
    clear_request_notification(connection)
    connection.status = Connection.Status.DECLINED
    connection.responded_at = timezone.now()
    connection.save(update_fields=["status", "responded_at"])


# --- Messages ---


def conversation_for(user, pk):
    """The accepted connection `pk` if `user` is one of its two people, else None."""
    return (
        Connection.objects.filter(
            Q(requester=user) | Q(addressee=user),
            pk=pk,
            status=Connection.Status.ACCEPTED,
        )
        .select_related(
            "requester__profile",
            "requester__photo",
            "addressee__profile",
            "addressee__photo",
        )
        .first()
    )


def conversations_of(user):
    """Each accepted connection of `user` with its latest message and unread count.

    Returns a list of (connection, last_message or None, unread_count), most recent activity first.
    """
    connections = list(
        Connection.objects.filter(
            Q(requester=user) | Q(addressee=user), status=Connection.Status.ACCEPTED
        )
        .select_related(
            "requester__profile",
            "requester__photo",
            "addressee__profile",
            "addressee__photo",
        )
        .annotate(
            last_message_id=Subquery(
                Message.objects.filter(connection=OuterRef("pk"))
                .order_by("-id")
                .values("id")[:1]
            )
        )
    )
    last = Message.objects.in_bulk(
        [c.last_message_id for c in connections if c.last_message_id]
    )
    unread = dict(
        Message.objects.filter(
            connection__in=connections, read_at__isnull=True, deleted_at__isnull=True
        )
        .exclude(sender=user)
        .values_list("connection")
        .annotate(total=Count("id"))
    )
    rows = [(c, last.get(c.last_message_id), unread.get(c.id, 0)) for c in connections]
    rows.sort(
        key=lambda r: r[1].created_at if r[1] else r[0].responded_at, reverse=True
    )
    return rows


def messages_in(connection, before=None, limit=50):
    """(messages oldest first, has_more): the newest `limit` messages, or the ones before id `before`."""
    queryset = connection.messages.order_by("-id")
    if before is not None:
        queryset = queryset.filter(id__lt=before)
    page = list(queryset[: limit + 1])
    return page[:limit][::-1], len(page) > limit


def send_message(connection, sender, body="", sticker="", image=None, file=None):
    """Save a message.

    `image` is (jpeg bytes, width, height) from images.process_chat_image, `file` is (bytes, name,
    content type) from files.process_chat_file; either may be None.
    """
    with transaction.atomic():
        message = Message.objects.create(
            connection=connection,
            sender=sender,
            body=body,
            sticker=sticker,
            has_image=image is not None,
            has_file=file is not None,
        )
        if image is not None:
            data, width, height = image
            MessageImage.objects.create(
                message=message, data=data, width=width, height=height
            )
        if file is not None:
            data, name, content_type = file
            MessageFile.objects.create(
                message=message,
                name=name,
                content_type=content_type,
                size=len(data),
                data=data,
            )

    # The conversation has two people; the one who did not send this is the receiver.
    receiver = (
        connection.addressee
        if connection.requester_id == sender.id
        else connection.requester
    )

    create_notification(
        user=receiver,
        title=f"New message from {sender.full_name}",
        body=body[:100]
        or (
            "Sent you a sticker"
            if sticker
            else "Sent you a photo"
            if image is not None
            else "Sent you a file"
        ),
        category=Notification.Category.CHAT_MESSAGE,
        data={
            "url": f"/dashboard/deaf/messages?c={connection.id}",
            "connection_id": connection.id,
        },
    )
    events.announce_message(connection, message)
    return message


def delete_message(connection, message, by):
    """Take a message back for both people. Only the person who sent it can.

    The text, sticker, photo and file are removed; the row stays so the chat can show "message deleted".
    Deleting twice is fine and does nothing the second time.
    """
    if message.sender_id != by.id:
        raise CannotConnect("You can only delete your own messages.")
    if message.deleted_at is not None:
        return message
    with transaction.atomic():
        MessageImage.objects.filter(message=message).delete()
        MessageFile.objects.filter(message=message).delete()
        message.body = ""
        message.sticker = ""
        message.has_image = False
        message.has_file = False
        message.deleted_at = timezone.now()
        message.save(
            update_fields=["body", "sticker", "has_image", "has_file", "deleted_at"]
        )
        events.announce_deleted(connection, message.id)
    return message


def mark_read(connection, reader):
    """Mark everything the other person sent as read. Returns how many were unread."""
    unread = connection.messages.filter(
        read_at__isnull=True, deleted_at__isnull=True
    ).exclude(sender=reader)
    ids = list(unread.values_list("id", flat=True))
    if not ids:
        return 0
    with transaction.atomic():
        count = connection.messages.filter(id__in=ids).update(read_at=timezone.now())
        events.announce_read(connection, reader, ids)
    return count
