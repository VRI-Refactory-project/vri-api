"""Rules for who can connect with whom. Views call these so the rules live in one place."""

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from .models import Connection

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
