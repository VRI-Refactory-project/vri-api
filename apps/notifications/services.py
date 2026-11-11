import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.utils import timezone
from django.db import transaction


from apps.notifications.models import Notification, PushDevice

from apps.notifications.models import (
    Notification,
    NotificationPreference,
    PushDevice,
)


from apps.notifications.web_push import send_web_push

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


# Map notification categories to their preference settings.
PREFERENCE_FOR_CATEGORY = {
    Notification.Category.INCOMING_REQUEST: "session_requests",
    Notification.Category.OFFER_REMINDER: "session_requests",
}


def send_expo_push(notification):
    """Send a saved notification to active Expo devices."""

    if not getattr(settings, "EXPO_PUSH_ENABLED", False):
        return

    devices = list(
        PushDevice.objects.filter(
            user=notification.user,
            is_active=True,
        )
    )

    if not devices:
        return

    messages = [
        {
            "to": device.token,
            "title": notification.title,
            "body": notification.body,
            "data": {
                "notification_id": notification.pk,
                "category": notification.category,
                **notification.data,
            },
            "sound": "default",
            "priority": "high",
        }
        for device in devices
    ]

    headers = {"Content-Type": "application/json"}

    access_token = getattr(settings, "EXPO_ACCESS_TOKEN", "")
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    request = Request(
        EXPO_PUSH_URL,
        data=json.dumps(messages).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urlopen(request, timeout=5) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        logger.exception(
            "Expo push delivery failed for notification %s",
            notification.pk,
        )
        return

    tickets = result.get("data", [])
    if isinstance(tickets, dict):
        tickets = [tickets]

    for device, ticket in zip(devices, tickets):
        details = ticket.get("details", {})
        if (
            ticket.get("status") == "error"
            and details.get("error") == "DeviceNotRegistered"
        ):
            device.is_active = False
            device.save(update_fields=["is_active", "updated_at"])


def wants_push(notification):
    """Check whether the user wants this notification category."""

    field = PREFERENCE_FOR_CATEGORY.get(notification.category)

    if field is None:
        return True

    preference = NotificationPreference.objects.filter(user=notification.user).first()

    if preference is None:
        return NotificationPreference._meta.get_field(field).default

    return getattr(preference, field)


def send_pushes(notification):
    """Send notifications to the user's phones and browsers."""

    if not wants_push(notification):
        return

    send_expo_push(notification)
    send_web_push(notification)
    

def create_notification(
    *,
    user,
    title,
    body,
    category=Notification.Category.GENERAL,
    data=None,
):
    """Save a notification and send pushes after transaction commit."""

    notification = Notification.objects.create(
        user=user,
        title=title,
        body=body,
        category=category,
        data=data or {},
    )

    transaction.on_commit(lambda: send_pushes(notification))
    return notification


def mark_notification_read(notification):
    """Mark a notification as read without changing its original timestamp."""

    if notification.read_at is None:
        notification.read_at = timezone.now()
        notification.save(update_fields=["read_at"])

    return notification
