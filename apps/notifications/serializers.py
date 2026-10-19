from rest_framework import serializers

from apps.notifications.models import Notification, PushDevice


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
        ]
        read_only_fields = fields
