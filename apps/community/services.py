"""Rules for who can connect with whom. Views call these so the rules live in one place."""

from django.contrib.auth import get_user_model
from django.db.models import Count, OuterRef, Q, Subquery
from django.utils import timezone

from .models import Connection, Message

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


def send_request(requester, target):
    """Ask `target` to connect. Returns (connection, accepted_straight_away)."""
    if target.id == requester.id:
        raise CannotConnect("You cannot connect with yourself.")
    existing = between(requester, target)
    if existing is None:
        return Connection.objects.create(requester=requester, addressee=target), False
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
    return Connection.objects.create(requester=requester, addressee=target), False


def accept(connection):
    connection.status = Connection.Status.ACCEPTED
    connection.responded_at = timezone.now()
    connection.save(update_fields=["status", "responded_at"])


def decline(connection):
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
        Message.objects.filter(connection__in=connections, read_at__isnull=True)
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


def send_message(connection, sender, body):
    return Message.objects.create(connection=connection, sender=sender, body=body)


def mark_read(connection, reader):
    """Mark everything the other person sent as read. Returns how many were unread."""
    return (
        connection.messages.filter(read_at__isnull=True)
        .exclude(sender=reader)
        .update(read_at=timezone.now())
    )
