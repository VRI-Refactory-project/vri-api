from rest_framework import serializers

from .models import CertificationDocument, InterpreterProfile, VerificationStatus


class CertificationDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = CertificationDocument
        fields = (
            "id",
            "document",
            "document_name",
            "uploaded_at",
            "rejection_reason",
        )
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
    username = serializers.CharField(source="user.username", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)

    class Meta:
        model = InterpreterProfile
        fields = (
            "id",
            "username",
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
