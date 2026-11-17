
from rest_framework import serializers

from apps.realtime.models import SessionFeedback, VideoSession


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
    category = serializers.ChoiceField(
        choices=SessionFeedback.Category.choices, required=False, allow_blank=True
    )
    issues = serializers.ListField(
        child=serializers.CharField(max_length=80), required=False, max_length=10
    )
    comment = serializers.CharField(required=False, allow_blank=True, max_length=1000)

    class Meta:
        model = SessionFeedback
        fields = [
            "rating",
            "category",
            "issues",
            "comment",
            "accuracy_comment",
            "clarity_comment",
            "created_at",
        ]
        read_only_fields = ["created_at"]


class SessionHistorySerializer(serializers.ModelSerializer):
    """Return safe metadata for a user's completed video sessions."""

    date = serializers.DateTimeField(
        source="connected_at",
        format="%Y-%m-%d",
        read_only=True,
    )
    started_at = serializers.DateTimeField(
        source="connected_at",
        read_only=True,
    )
    duration_minutes = serializers.SerializerMethodField()
    initiator = serializers.SerializerMethodField()
    other_participant = serializers.SerializerMethodField()

    class Meta:
        model = VideoSession
        fields = [
            "id",
            "date",
            "started_at",
            "ended_at",
            "duration_minutes",
            "initiator",
            "other_participant",
        ]

    def get_duration_minutes(self, obj):
        if obj.connected_at is None or obj.ended_at is None:
            return None

        seconds = (obj.ended_at - obj.connected_at).total_seconds()
        return max(1, round(seconds / 60))

    def get_initiator(self, obj):
        # The backend does not yet record who explicitly initiated the call.
        return None

    def get_other_participant(self, obj):
        request = self.context["request"]
        service_request = obj.service_request

        if service_request.requester_id == request.user.pk:
            interpreter = service_request.interpreter
            if interpreter is None:
                return None

            return {"name": interpreter.user.full_name}

        return {"name": service_request.requester.full_name}
