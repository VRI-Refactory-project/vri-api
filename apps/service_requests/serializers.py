from drf_spectacular.utils import extend_schema_field, inline_serializer
from django.utils import timezone
from rest_framework import serializers

from apps.interpreters.models import Specialisation
from apps.realtime.call_status import display_status
from apps.service_requests.models import RequestOffer, ServiceRequest, SessionInvitation
from apps.users.photos import photo_path

# The contexts a requester can choose. "Other" is an interpreter specialisation only.
REQUEST_CONTEXTS = (
    Specialisation.GENERAL,
    Specialisation.MEDICAL,
    Specialisation.LEGAL,
)


class ServiceRequestSerializer(serializers.ModelSerializer):
    """What the person who asked sees. Who an offer went to is not shown until they accept."""

    requester_id = serializers.IntegerField(source="requester.id", read_only=True)
    interpreter_id = serializers.SerializerMethodField()
    interpreter = serializers.SerializerMethodField()
    ended_at = serializers.SerializerMethodField()
    # How the call is going (waiting, connecting, connected, ended, failed). Apps show this; `status` is unchanged.
    display_status = serializers.SerializerMethodField()
    # V2-013: the institution a staff member asked for; null for Deaf users. Set by the server, never sent.
    institution = serializers.SerializerMethodField()

    class Meta:
        model = ServiceRequest
        fields = (
            "id",
            "requester_id",
            "institution",
            "interpreter_id",
            "interpreter",
            "specialisation_required",
            "description",
            "priority",
            "request_key",
            "status",
            "display_status",
            "assigned_at",
            "ended_at",
            "cancelled_at",
            "cancel_reason",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "requester_id",
            "institution",
            "interpreter_id",
            "interpreter",
            "status",
            "display_status",
            "assigned_at",
            "ended_at",
            "cancelled_at",
            "cancel_reason",
            "created_at",
            "updated_at",
        )
        extra_kwargs = {"request_key": {"write_only": True, "required": False}}

    def get_display_status(self, obj):
        return display_status(obj)

    @extend_schema_field(
        inline_serializer(
            "RequestInstitution",
            {"id": serializers.IntegerField(), "name": serializers.CharField()},
            allow_null=True,
        )
    )
    def get_institution(self, obj):
        if obj.institution is None:
            return None
        return {"id": obj.institution.pk, "name": obj.institution.organisation_name}

    # Who interpreted: shown while the session runs and once it is completed, so a finished session
    # still says who it was with. A request that was cancelled, failed or is still looking has none.
    SHOWS_INTERPRETER = (ServiceRequest.Status.ASSIGNED, ServiceRequest.Status.COMPLETED)

    def get_interpreter_id(self, obj):
        if obj.status not in self.SHOWS_INTERPRETER:
            return None
        return obj.interpreter_id

    def get_interpreter(self, obj):
        if obj.status not in self.SHOWS_INTERPRETER or obj.interpreter is None:
            return None
        profile = obj.interpreter
        return {
            "name": profile.user.full_name,
            "languages": ", ".join(profile.sign_languages or []),
        }

    def get_ended_at(self, obj):
        video = getattr(obj, "video_session", None)
        return (video.ended_at if video else None) or obj.ended_at

    def validate_specialisation_required(self, value):
        if value not in REQUEST_CONTEXTS:
            raise serializers.ValidationError("Choose General, Medical or Legal.")
        return value


class InterpreterSessionSerializer(serializers.ModelSerializer):
    """A session the signed-in interpreter took: who it was with, and when it started and ended."""

    requester_name = serializers.CharField(source="requester.full_name", read_only=True)
    requester_photo = serializers.SerializerMethodField()
    duration_minutes = serializers.SerializerMethodField()

    class Meta:
        model = ServiceRequest
        fields = (
            "id",
            "status",
            "specialisation_required",
            "priority",
            "requester_name",
            "requester_photo",
            "assigned_at",
            "ended_at",
            "duration_minutes",
        )

    def get_requester_photo(self, obj) -> str | None:
        return photo_path(obj.requester)

    def get_duration_minutes(self, obj) -> int | None:
        return session_duration_minutes(obj)


def session_duration_minutes(request) -> int | None:
    """How long a session has lasted, in whole minutes. None if it never started."""
    if request.assigned_at is None:
        return None
    ended_at = request.ended_at or timezone.now()
    return max(1, round((ended_at - request.assigned_at).total_seconds() / 60))


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
        # Each interpreter has their own offer, so the countdown is the viewer's own offer's.
        offers = obj.offers.filter(outcome=RequestOffer.Outcome.PENDING)
        viewer = self.context.get("user")
        if viewer is not None:
            offers = offers.filter(interpreter__user=viewer)
        offer = offers.first()
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
        # Who is asking: the institution a staff member asked for (V2-013), otherwise their name.
        service_request = offer.service_request
        if service_request.institution is not None:
            return service_request.institution.organisation_name
        return service_request.requester.full_name

    def get_sector(self, offer) -> str:
        return offer.service_request.get_specialisation_required_display()

    def get_language(self, offer) -> str:
        return SESSION_LANGUAGE

    def get_participants(self, offer) -> str:
        return SESSION_PARTICIPANTS


class OfferResponseSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["accept", "decline"])


class OfferStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=sorted(set(OFFER_STATUS_FOR_SCREEN.values()))
    )


class CallTokenSerializer(serializers.Serializer):
    """What an app needs to join a session's call."""

    token = serializers.CharField(
        help_text="LiveKit access token for this person and room."
    )
    url = serializers.CharField(help_text="LiveKit server address to connect to.")
    room = serializers.CharField(help_text="The session's room name.")
    ice_servers = serializers.ListField(
        child=serializers.DictField(),
        help_text="STUN and TURN servers the app should use to connect (may be empty).",
    )


class CreateInvitationSerializer(serializers.ModelSerializer):
    """What the Deaf user sends to invite a hearing guest."""

    class Meta:
        model = SessionInvitation
        fields = ["email", "phone"]  # phone is optional (blank allowed)


class InvitationSerializer(serializers.ModelSerializer):
    """What the Deaf user sees: who was invited, whether they joined, whether they were removed."""

    class Meta:
        model = SessionInvitation
        fields = ["id", "email", "phone", "created_at", "joined_at", "removed_at"]
        read_only_fields = fields
