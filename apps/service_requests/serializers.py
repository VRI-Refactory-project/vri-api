from rest_framework import serializers

from apps.core.permissions import user_institution
from apps.interpreters.models import Specialisation
from apps.service_requests.models import RequestOffer, ServiceRequest

# The contexts a requester can choose. "Other" is an interpreter specialisation only.
REQUEST_CONTEXTS = (Specialisation.GENERAL, Specialisation.MEDICAL, Specialisation.LEGAL)


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
            "cancelled_at",
            "cancel_reason",
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
            "cancelled_at",
            "cancel_reason",
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
        if value not in REQUEST_CONTEXTS:
            raise serializers.ValidationError("Choose General, Medical or Legal.")
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


# V2-010: the interpreter's offers screen (vri-web RequestsPage) was built first, so these responses use
# the exact names and values it reads (camelCase, lowercase statuses). Do not rename them to match the
# rest of the API without changing the screen too.
OFFER_STATUS_FOR_SCREEN = {
    RequestOffer.Outcome.PENDING: "pending",
    RequestOffer.Outcome.ACCEPTED: "accepted",
    RequestOffer.Outcome.DECLINED: "declined",
    RequestOffer.Outcome.EXPIRED: "expired",
    # The screen has no "withdrawn": either way the offer is gone from the interpreter's list.
    RequestOffer.Outcome.WITHDRAWN: "rerouted",
}
SESSION_LANGUAGE = "Uganda Sign Language ⇄ English"
SESSION_PARTICIPANTS = "Deaf user and hearing participant"


class InterpreterOfferSerializer(serializers.Serializer):
    """One pending offer on the interpreter's offers screen."""

    id = serializers.SerializerMethodField()
    title = serializers.SerializerMethodField()
    institutionName = serializers.SerializerMethodField()
    sector = serializers.SerializerMethodField()
    language = serializers.SerializerMethodField()
    participants = serializers.SerializerMethodField()
    expiresAt = serializers.DateTimeField(source="expires_at")

    def get_id(self, offer) -> str:
        return str(offer.pk)

    def get_title(self, offer) -> str:
        service_request = offer.service_request
        title = f"{service_request.get_specialisation_required_display()} interpreting request"
        if service_request.priority == ServiceRequest.Priority.EMERGENCY:
            return f"Emergency: {title}"
        return title

    def get_institutionName(self, offer) -> str:
        # Who is asking: their institution when they are institution staff, otherwise their name.
        requester = offer.service_request.requester
        institution = user_institution(requester)
        return institution.organisation_name if institution else requester.full_name

    def get_sector(self, offer) -> str:
        return offer.service_request.get_specialisation_required_display()

    def get_language(self, offer) -> str:
        return SESSION_LANGUAGE

    def get_participants(self, offer) -> str:
        return SESSION_PARTICIPANTS


class OfferResponseSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["accept", "decline"])


class OfferStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=sorted(set(OFFER_STATUS_FOR_SCREEN.values())))
