import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.utils import timezone

from apps.notifications.models import Notification, PushDevice

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"


def send_expo_push(notification):
    """Send a saved notification to the user's active Expo devices."""
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


def create_notification(
    *,
    user,
    title,
    body,
    category=Notification.Category.GENERAL,
    data=None,
):
    """Save a notification and attempt push delivery."""
    notification = Notification.objects.create(
        user=user,
        title=title,
        body=body,
        category=category,
        data=data or {},
    )

    send_expo_push(notification)
    return notification


def mark_notification_read(notification):
    """Mark a notification as read without changing its original timestamp."""
    if notification.read_at is None:
        notification.read_at = timezone.now()
        notification.save(update_fields=["read_at"])

    return notification
