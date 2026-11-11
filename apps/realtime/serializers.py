
from rest_framework import serializers

from apps.realtime.models import SessionFeedback


class TicketSerializer(serializers.Serializer):
    """What an app needs to open a WebSocket."""

    ticket = serializers.CharField(
        help_text="Send as ?ticket= when opening the WebSocket."
    )
    expires_in = serializers.IntegerField(
        help_text="Seconds the ticket can be used for."
    )


class ConnectionQualityTestSerializer(serializers.Serializer):
    """Validate client-reported network quality measurements."""

    latency_ms = serializers.FloatField(min_value=0)
    jitter_ms = serializers.FloatField(min_value=0)
    packet_loss_percent = serializers.FloatField(min_value=0, max_value=100)
    upload_bandwidth_kbps = serializers.FloatField(min_value=0)
    download_bandwidth_kbps = serializers.FloatField(min_value=0)


class SessionFeedbackSerializer(serializers.ModelSerializer):
    """Validate feedback submitted for a completed video session."""

    rating = serializers.IntegerField(min_value=1, max_value=5)

    class Meta:
        model = SessionFeedback
        fields = [
            "rating",
            "accuracy_comment",
            "clarity_comment",
            "created_at",
        ]
        read_only_fields = ["created_at"]
