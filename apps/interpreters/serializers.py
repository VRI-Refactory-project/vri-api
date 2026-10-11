from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import serializers

from apps.accounts.serializers import BaseRegistrationSerializer

from .models import (
    CertificationDocument,
    InterpreterProfile,
    Specialisation,
    VerificationDecision,
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
        # fill document_name from the uploaded file's name
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


class InterpreterRegistrationSerializer(BaseRegistrationSerializer):
    """V2-003 step 1: account details. An email is required (certification results are sent to it).

    The account starts INACTIVE / pending review: the profile is created with the default
    verification status, so the interpreter cannot receive requests until an admin approves them.
    """

    role = get_user_model().Role.INTERPRETER
    email_required = True
    specialisation = serializers.ChoiceField(
        choices=Specialisation.choices,
        required=False,
        default=Specialisation.GENERAL,)

    def create_profile(self, user, validated_data):
        InterpreterProfile.objects.create(
            user=user,
            specialisation=validated_data["specialisation"],
        )


# --- V2-045: admin verification (read side) ---


class VerificationDecisionSerializer(serializers.ModelSerializer):
    admin_name = serializers.CharField(source="admin.full_name", read_only=True)

    class Meta:
        model = VerificationDecision
        fields = (
            "id",
            "action",
            "reason",
            "previous_status",
            "new_status",
            "admin",
            "admin_name",
            "created_at",
        )
        read_only_fields = fields


class ManageCertificationSerializer(serializers.ModelSerializer):
    """A certificate as an admin sees it. `file_url` is the protected download endpoint, never a public file path."""

    file_url = serializers.SerializerMethodField()

    class Meta:
        model = CertificationDocument
        fields = (
            "id",
            "document_name",
            "certificate_type",
            "issuing_organisation",
            "uploaded_at",
            "rejection_reason",
            "file_url",
        )
        read_only_fields = fields

    def get_file_url(self, obj) -> str:
        url = reverse(
            "manage-interpreters:certification-file",
            args=[obj.interpreter_id, obj.pk],
        )
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url


class ManageInterpreterListSerializer(serializers.ModelSerializer):
    """One row of the verification queue."""

    user_id = serializers.IntegerField(source="user.id", read_only=True)
    full_name = serializers.CharField(source="user.full_name", read_only=True)
    phone_number = serializers.CharField(
        source="user.phone_number",
        read_only=True,
    )
    email = serializers.EmailField(source="user.email", read_only=True)
    certification_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = InterpreterProfile
        fields = (
            "id",
            "user_id",
            "full_name",
            "phone_number",
            "email",
            "verification_status",
            "specialisation",
            "certification_count",
            "created_at",
        )
        read_only_fields = fields


class ManageInterpreterDetailSerializer(ManageInterpreterListSerializer):
    """The review page: the interpreter, their certificates and every decision made so far."""

    certifications = ManageCertificationSerializer(many=True, read_only=True)
    decisions = VerificationDecisionSerializer(many=True, read_only=True)

    class Meta(ManageInterpreterListSerializer.Meta):
        fields = (
            *ManageInterpreterListSerializer.Meta.fields,
            "is_available",
            "certifications",
            "decisions",
        )
        read_only_fields = fields
