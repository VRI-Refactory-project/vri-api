"""Short-lived tickets for opening a WebSocket (V2-011).

A WebSocket cannot carry the Authorization header, and the login token must not go in a URL, where it
would end up in server logs. So a signed-in app swaps its login for a ticket that lasts a minute.
"""

from django.contrib.auth import get_user_model
from django.core import signing

TICKET_MAX_AGE_SECONDS = 60
_SALT = "realtime.ticket"


def issue_ticket(user):
    return signing.dumps({"user": user.pk}, salt=_SALT)


def user_from_ticket(ticket):
    """The active user a ticket was issued to, or None when it is changed, expired or not a ticket."""
    try:
        data = signing.loads(ticket, salt=_SALT, max_age=TICKET_MAX_AGE_SECONDS)
    except signing.BadSignature:  # also covers expired tickets
        return None
    return get_user_model().objects.filter(pk=data["user"], is_active=True).first()
