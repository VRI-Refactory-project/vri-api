
from rest_framework import serializers

from apps.interpreters.models import Specialisation
from apps.service_requests.models import ServiceRequest


class ServiceRequestSerializer(serializers.ModelSerializer):
    requester_id = serializers.IntegerField(source="requester.id", read_only=True)
    interpreter_id = serializers.IntegerField(
        source="interpreter.id",
        read_only=True,
        allow_null=True,
    )

    class Meta:
        model = ServiceRequest
        fields = (
            "id",
            "requester_id",
            "interpreter_id",
            "specialisation_required",
            "description",
            "status",
            "assigned_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "requester_id",
            "interpreter_id",
            "status",
            "assigned_at",
            "created_at",
            "updated_at",
        )

    def validate_specialisation_required(self, value):
        if value not in Specialisation.values:
            raise serializers.ValidationError(
                "Select a valid interpreter specialisation."
            )
        return value
