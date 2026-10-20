from apps.interpreters.models import Specialisation
from rest_framework import serializers

from .models import SessionRequest
from .services import queue_position as get_queue_position


class SessionRequestCreateSerializer(serializers.ModelSerializer):
    """Serializer used when a Deaf user or institutional requester creates a new request.
    Validates input fields and accepts idempotency keys for network retries.
    """

    specialisation = serializers.ChoiceField(
        choices=Specialisation.choices,
        default=Specialisation.GENERAL,
        required=False,
    )
    description = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
    )
    is_emergency = serializers.BooleanField(
        default=False,
        required=False,
    )
    request_key = serializers.CharField(
        required=False,
        allow_null=True,
        write_only=True,
        max_length=64,
    )

    class Meta:
        model = SessionRequest
        fields = (
            "specialisation",
            "description",
            "is_emergency",
            "request_key",
        )

    def validate_specialisation(self, value):
        if value not in Specialisation.values:
            raise serializers.ValidationError(
                "Select a valid interpreter specialisation (general, medical, or legal)."
            )
        return value


class SessionRequestSerializer(serializers.ModelSerializer):
    """Complete detail serializer for SessionRequest data returned to clients.
    Exposes participant IDs, status labels, queue metrics, and timestamps.
    """

    requester_id = serializers.IntegerField(source="requester.id", read_only=True)
    offered_interpreter_id = serializers.IntegerField(
        source="offered_interpreter.id",
        read_only=True,
        allow_null=True,
    )
    accepted_interpreter_id = serializers.IntegerField(
        source="accepted_interpreter.id",
        read_only=True,
        allow_null=True,
    )
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    queue_position = serializers.SerializerMethodField()

    class Meta:
        model = SessionRequest
        fields = (
            "id",
            "requester_id",
            "offered_interpreter_id",
            "accepted_interpreter_id",
            "specialisation",
            "description",
            "is_emergency",
            "status",
            "status_label",
            "queue_position",
            "matched_by_fallback",
            "created_at",
            "queued_at",
            "offered_at",
            "accepted_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_queue_position(self, obj):
        """Returns the integer position in queue if status is QUEUED, otherwise None."""
        return get_queue_position(obj)


class SessionStatusSerializer(serializers.ModelSerializer):
    """Lightweight serializer for mobile app polling (GET /api/v1/sessions/)."""

    status_label = serializers.CharField(source="get_status_display", read_only=True)
    queue_position = serializers.SerializerMethodField()

    class Meta:
        model = SessionRequest
        fields = (
            "id",
            "status",
            "status_label",
            "queue_position",
        )
        read_only_fields = fields

    def get_queue_position(self, obj):
        return get_queue_position(obj)
