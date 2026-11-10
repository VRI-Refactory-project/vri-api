from rest_framework import serializers

from apps.notifications.models import Notification, PushDevice
from apps.service_requests.models import ServiceRequest
from apps.users.photos import photo_path


class PushDeviceRegistrationSerializer(serializers.ModelSerializer):
    class Meta:
        model = PushDevice
        fields = ["token", "platform"]
        extra_kwargs = {
            "token": {"validators": []},
        }

    def validate_token(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("A push token is required.")
        return value


class NotificationSerializer(serializers.ModelSerializer):
    # The Deaf user behind a new-request notification, so the app can show their name and photo.
    actor_name = serializers.SerializerMethodField()
    actor_photo = serializers.SerializerMethodField()

    def _requester(self, notification):
        """The Deaf user who made the request, or None for any other kind of notification."""
        if hasattr(
            notification, "_requester_cache"
        ):  # look it up once per notification
            return notification._requester_cache

        requester = None
        request_id = notification.data.get("request_id")
        if (
            notification.category == Notification.Category.INCOMING_REQUEST
            and request_id
        ):
            service_request = (
                ServiceRequest.objects.select_related("requester__photo")
                .filter(pk=request_id)
                .first()
            )
            requester = service_request.requester if service_request else None

        notification._requester_cache = requester
        return requester

    def get_actor_name(self, notification) -> str | None:
        requester = self._requester(notification)
        return (requester.full_name or None) if requester else None

    def get_actor_photo(self, notification) -> str | None:
        requester = self._requester(notification)
        return photo_path(requester) if requester else None

    class Meta:
        model = Notification
        fields = [
            "id",
            "title",
            "body",
            "category",
            "data",
            "read_at",
            "created_at",
            "actor_name",
            "actor_photo",
        ]
        read_only_fields = fields
