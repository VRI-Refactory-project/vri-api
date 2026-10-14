from django.contrib.auth import get_user_model
from rest_framework import serializers

from apps.accounts.serializers import BaseRegistrationSerializer

from .models import (
    CertificationDocument,
    InterpreterAvailability,
    InterpreterProfile,
    Specialisation,
    VerificationStatus,
)


class CertificationDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = CertificationDocument
        fields = (
            "id",
            "document",
            "document_name",
            "certificate_type",
            "issuing_organisation",
            "uploaded_at",
            "rejection_reason",
        )
        extra_kwargs = {
            "certificate_type": {"required": True, "allow_blank": False},
            "issuing_organisation": {"required": True, "allow_blank": False},
        }
        read_only_fields = (
            "id",
            "document_name",
            "uploaded_at",
            "rejection_reason",
        )

    def create(self, validated_data):
        # Fill document_name from the uploaded file's name
        if "document" in validated_data:
            validated_data["document_name"] = validated_data["document"].name
        return super().create(validated_data)


class InterpreterProfileSerializer(serializers.ModelSerializer):
    certifications = CertificationDocumentSerializer(many=True, read_only=True)
    full_name = serializers.CharField(source="user.full_name", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)

    class Meta:
        model = InterpreterProfile
        fields = (
            "id",
            "full_name",
            "email",
            "verification_status",
            "is_available",
            "specialisation",
            "specialisation_other",
            "sign_languages",
            "working_hours_schedule",
            "certifications",
            "created_at",
        )
        read_only_fields = ("id", "verification_status", "created_at")

    def validate_is_available(self, value):
        # self.instance is the profile being updated
        if value and (
            self.instance is None
            or self.instance.verification_status != VerificationStatus.APPROVED
        ):
            raise serializers.ValidationError(
                "You can only set yourself available after your profile is approved."
            )
        return value

    def validate(self, attrs):
        specialisation = attrs.get(
            "specialisation",
            getattr(self.instance, "specialisation", Specialisation.GENERAL),
        )
        specialisation_other = attrs.get(
            "specialisation_other",
            getattr(self.instance, "specialisation_other", ""),
        )

        # Enforce free-text entry if 'OTHER' is selected
        if specialisation == Specialisation.OTHER and not specialisation_other.strip():
            raise serializers.ValidationError(
                {
                    "specialisation_other": (
                        "Please specify your specialisation when 'OTHER' is selected."
                    )
                }
            )
        return attrs


class InterpreterAvailabilitySerializer(serializers.ModelSerializer):
    class Meta:
        model = InterpreterAvailability
        fields = (
            "id",
            "start_at",
            "end_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def validate(self, attrs):
        start_at = attrs.get(
            "start_at",
            self.instance.start_at if self.instance else None,
        )
        end_at = attrs.get(
            "end_at",
            self.instance.end_at if self.instance else None,
        )

        if start_at and end_at and end_at <= start_at:
            raise serializers.ValidationError({
                "end_at": "End time must be later than start time."
            })

        if start_at and end_at:
            profile = self.context["availability_profile"]
            overlapping = InterpreterAvailability.objects.filter(
                interpreter=profile,
                start_at__lt=end_at,
                end_at__gt=start_at,
            )

            if self.instance:
                overlapping = overlapping.exclude(pk=self.instance.pk)

            if overlapping.exists():
                raise serializers.ValidationError({
                    "non_field_errors": [
                        "This period overlaps an existing availability period."
                    ]
                })

        return attrs


class InterpreterRegistrationSerializer(BaseRegistrationSerializer):
    """V2-003 step 1: account details.

    An email is required (certification results are sent to it). The account starts
    INACTIVE / pending review: the profile is created with the default verification status,
    so the interpreter cannot receive requests until an admin approves them.
    """

    role = get_user_model().Role.INTERPRETER
    email_required = True
    specialisation = serializers.ChoiceField(
        choices=Specialisation.choices,
        required=False,
        default=Specialisation.GENERAL,
    )

    def create_profile(self, user, validated_data):
        InterpreterProfile.objects.create(
            user=user,
            specialisation=validated_data["specialisation"],
        )


# ---- V2-021 availability schedule (new) ----


class WeekQuerySerializer(serializers.Serializer):
    """The ?week_start= value. Any date inside the week works (YYYY-MM-DD)."""

    week_start = serializers.DateField(required=False)


class DayScheduleSerializer(serializers.Serializer):
    """One day on the schedule screen (weekday: 0 = Monday ... 6 = Sunday)."""

    date = serializers.DateField()
    weekday = serializers.IntegerField()
    is_available = serializers.BooleanField()
    start_time = serializers.TimeField(allow_null=True)
    end_time = serializers.TimeField(allow_null=True)
    source = serializers.ChoiceField(choices=["default", "exception"])


class WeekScheduleSerializer(serializers.Serializer):
    week_start = serializers.DateField()
    week_end = serializers.DateField()
    days = DayScheduleSerializer(many=True)


class DayUpdateSerializer(serializers.Serializer):
    """What the pencil card sends when the interpreter saves one day.

    repeat_weekly False (the default): change only that date.
    repeat_weekly True: change that weekday in the default week.
    """

    is_available = serializers.BooleanField()
    start_time = serializers.TimeField(required=False, allow_null=True)
    end_time = serializers.TimeField(required=False, allow_null=True)
    repeat_weekly = serializers.BooleanField(default=False)

    def validate(self, attrs):
        if attrs["is_available"]:
            start = attrs.get("start_time")
            end = attrs.get("end_time")
            if start is None or end is None:
                raise serializers.ValidationError(
                    "start_time and end_time are required when you are available."
                )
            if end <= start:
                raise serializers.ValidationError(
                    {"end_time": "End time must be after start time."}
                )
        else:
            # Unavailable all day: no hours to store.
            attrs["start_time"] = None
            attrs["end_time"] = None
        return attrs


class AvailabilityStatusSerializer(serializers.Serializer):
    """What the dashboard shows.

    is_available: the dashboard switch.
    available_now: can she be offered a request at this very moment?
    reason: why not (null when available_now is true).
    """

    is_available = serializers.BooleanField()
    available_now = serializers.BooleanField()
    reason = serializers.ChoiceField(
        choices=["not_approved", "switched_off", "day_off", "outside_hours"],
        allow_null=True,
    )


class AvailabilityStatusUpdateSerializer(serializers.Serializer):
    """What the dashboard sends to flip the switch."""

    is_available = serializers.BooleanField()

    def validate_is_available(self, value):
        profile = self.context["profile"]
        if value and profile.verification_status != VerificationStatus.APPROVED:
            raise serializers.ValidationError(
                "You can only set yourself available after your profile is approved."
            )
        return value
