"""Browser notifications (Web Push): they arrive even when SignConnect is not open.

The browser gives the web app a subscription (an address plus two keys). The app sends it here, we
store it, and every notification we save is also pushed to the person's subscribed browsers.
"""

import json
import logging

from django.conf import settings
from pywebpush import WebPushException, webpush
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.notifications.models import WebPushSubscription

logger = logging.getLogger(__name__)


def web_push_ready() -> bool:
    """True when Web Push is switched on and the server has the keys it needs to send."""
    return bool(
        getattr(settings, "WEB_PUSH_ENABLED", False)
        and getattr(settings, "WEB_PUSH_VAPID_PUBLIC_KEY", "")
        and getattr(settings, "WEB_PUSH_VAPID_PRIVATE_KEY", "")
        and getattr(settings, "WEB_PUSH_CONTACT", "")
    )


def send_web_push(notification):
    """Send a saved notification to every browser the person turned notifications on in."""
    if not web_push_ready():
        return

    subscriptions = list(
        WebPushSubscription.objects.filter(user=notification.user, is_active=True)
    )
    if not subscriptions:
        return

    payload = json.dumps(
        {
            "notification_id": notification.pk,
            "title": notification.title,
            "body": notification.body,
            "category": notification.category,
            "data": notification.data,
        }
    )

    for subscription in subscriptions:
        try:
            webpush(
                subscription_info={
                    "endpoint": subscription.endpoint,
                    "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
                },
                data=payload,
                vapid_private_key=settings.WEB_PUSH_VAPID_PRIVATE_KEY,
                vapid_claims={
                    "sub": settings.WEB_PUSH_CONTACT
                },  # a new dict: webpush adds to it
                timeout=5,
            )
        except WebPushException as exc:
            code = getattr(exc.response, "status_code", None)
            if code in (
                404,
                410,
            ):  # the browser dropped the subscription, so stop sending to it
                subscription.is_active = False
                subscription.save(update_fields=["is_active", "updated_at"])
            else:
                logger.exception("Web push failed for notification %s", notification.pk)
        except Exception:
            logger.exception("Web push failed for notification %s", notification.pk)


class WebPushKeysSerializer(serializers.Serializer):
    p256dh = serializers.CharField(max_length=255)
    auth = serializers.CharField(max_length=255)


class WebPushSubscriptionSerializer(serializers.Serializer):
    """The shape of `subscription.toJSON()` in the browser."""

    endpoint = serializers.URLField(max_length=1000)
    keys = WebPushKeysSerializer()


class WebPushKeyView(APIView):
    """GET /api/v1/notifications/web-push/key/: the public key the browser needs to subscribe.
    It is empty when Web Push is off, which tells the web app to hide the switch."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        key = settings.WEB_PUSH_VAPID_PUBLIC_KEY if web_push_ready() else ""
        return Response({"public_key": key})


class WebPushSubscriptionView(APIView):
    """POST turns browser notifications on for this browser; DELETE turns them off."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = WebPushSubscriptionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # The address only exists in one browser. If someone else signs in on that browser, the
        # subscription moves to them, so the last person's notifications do not show up for the next.
        WebPushSubscription.objects.update_or_create(
            endpoint=data["endpoint"],
            defaults={
                "user": request.user,
                "p256dh": data["keys"]["p256dh"],
                "auth": data["keys"]["auth"],
                "is_active": True,
            },
        )
        return Response(
            {"detail": "Browser notifications are on."}, status=status.HTTP_200_OK
        )

    def delete(self, request):
        endpoint = request.data.get("endpoint")
        if not isinstance(endpoint, str) or not endpoint.strip():
            return Response(
                {"endpoint": "The browser's address is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        WebPushSubscription.objects.filter(
            user=request.user, endpoint=endpoint.strip(), is_active=True
        ).update(is_active=False)
        return Response(status=status.HTTP_204_NO_CONTENT)
