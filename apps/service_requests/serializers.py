from rest_framework import serializers

from apps.interpreters.models import Specialisation
from apps.service_requests.models import RequestOffer, ServiceRequest


class ServiceRequestSerializer(serializers.ModelSerializer):
    """What the person who asked sees. Who an offer went to is not shown until they accept."""

    requester_id = serializers.IntegerField(source="requester.id", read_only=True)
    interpreter_id = serializers.SerializerMethodField()
    interpreter = serializers.SerializerMethodField()

    class Meta:
        model = ServiceRequest
        fields = (
            "id",
            "requester_id",
            "interpreter_id",
            "interpreter",
            "specialisation_required",
            "description",
            "priority",
            "request_key",
            "status",
            "assigned_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "requester_id",
            "interpreter_id",
            "interpreter",
            "status",
            "assigned_at",
            "created_at",
            "updated_at",
        )
        extra_kwargs = {"request_key": {"write_only": True, "required": False}}

    def get_interpreter_id(self, obj):
        if obj.status != ServiceRequest.Status.ASSIGNED:
            return None
        return obj.interpreter_id

    def get_interpreter(self, obj):
        if obj.status != ServiceRequest.Status.ASSIGNED or obj.interpreter is None:
            return None
        profile = obj.interpreter
        return {
            "name": profile.user.full_name,
            "languages": ", ".join(profile.sign_languages or []),
        }

    def validate_specialisation_required(self, value):
        if value not in Specialisation.values:
            raise serializers.ValidationError(
                "Select a valid interpreter specialisation."
            )
        return value


class OfferedRequestSerializer(serializers.ModelSerializer):
    """What an interpreter sees about a request offered to them."""

    requester_name = serializers.CharField(source="requester.full_name", read_only=True)
    expires_at = serializers.SerializerMethodField()

    class Meta:
        model = ServiceRequest
        fields = (
            "id",
            "specialisation_required",
            "description",
            "priority",
            "requester_name",
            "offered_at",
            "expires_at",
            "matched_by_fallback",
        )

    def get_expires_at(self, obj):
        offer = obj.offers.filter(outcome=RequestOffer.Outcome.PENDING).first()
        return offer.expires_at if offer else None
