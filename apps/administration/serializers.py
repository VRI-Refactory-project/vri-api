from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.urls import reverse
from django.utils import timezone
from rest_framework import serializers

from apps.institutions.models import (
    InstitutionProfile,
    InstitutionStatus,
    InstitutionVerificationDocument,
)

from .models import AdminProfile

User = get_user_model()


class AdminProfileSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source="user.full_name", required=False)
    email = serializers.EmailField(source="user.email", required=False)
    phone_number = serializers.CharField(
        source="user.phone_number", required=False, allow_blank=True, default=""
    )
    contact_number = serializers.CharField(
        source="user.phone_number", required=False, allow_blank=True, default=""
    )
    profile_photo = serializers.ImageField(
        source="user.profile_photo", required=False, allow_null=True
    )

    class Meta:
        model = AdminProfile
        fields = (
            "id",
            "full_name",
            "email",
            "phone_number",
            "contact_number",
            "profile_photo",
            "session_alerts",
            "verification_alerts",
            "password_last_changed",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "password_last_changed",
            "created_at",
            "updated_at",
        )

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        user = instance.user
        phone_str = str(getattr(user, "phone_number", "") or "")
        ret["user"] = {
            "id": user.id,
            "full_name": getattr(
                user,
                "full_name",
                f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip(),
            ),
            "email": user.email,
            "phone_number": phone_str,
            "contact_number": phone_str,
        }
        return ret

    def validate_email(self, value):
        request = self.context.get("request")
        if request and hasattr(request, "user"):
            user = request.user
            if User.objects.exclude(pk=user.pk).filter(email__iexact=value).exists():
                raise serializers.ValidationError(
                    "An account with this email already exists."
                )
        return value.lower()

    def update(self, instance, validated_data):
        user_data = validated_data.pop("user", {})
        user = instance.user

        if "contact_number" in user_data and "phone_number" not in user_data:
            user_data["phone_number"] = user_data.pop("contact_number")

        if "full_name" in user_data:
            full_name_val = user_data.pop("full_name", "").strip()
            if hasattr(user, "full_name") and not isinstance(
                type(user).full_name, property
            ):
                user.full_name = full_name_val
            else:
                parts = full_name_val.split(" ", 1)
                user.first_name = parts[0]
                user.last_name = parts[1] if len(parts) > 1 else ""

        for attr, value in user_data.items():
            setattr(user, attr, value)
        user.save()

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        return instance


class AdminChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(required=True, write_only=True)
    new_password = serializers.CharField(required=True, write_only=True)
    confirm_password = serializers.CharField(required=True, write_only=True)

    def validate_old_password(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError(
                "Your current password was entered incorrectly."
            )
        return value

    def validate(self, attrs):
        if attrs["new_password"] != attrs["confirm_password"]:
            raise serializers.ValidationError(
                {"confirm_password": "New password and confirmation do not match."}
            )
        validate_password(attrs["new_password"], user=self.context["request"].user)
        return attrs

    def save(self, **kwargs):
        user = self.context["request"].user
        new_password = self.validated_data["new_password"]
        user.set_password(new_password)
        user.save()

        profile, _ = AdminProfile.objects.get_or_create(user=user)
        profile.password_last_changed = timezone.now()
        profile.save()
        return profile


class AdminInstitutionSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source="organisation_name", read_only=True)
    registration_number = serializers.CharField(read_only=True)
    sector = serializers.CharField(source="organisation_type", read_only=True)
    sector_other = serializers.CharField(source="organisation_type_other", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    phone = serializers.CharField(source="user.phone_number", read_only=True)
    contact_name = serializers.CharField(source="user.full_name", read_only=True)
    email_verified = serializers.BooleanField(
        source="user.email_verified", read_only=True
    )
    is_active = serializers.BooleanField(source="user.is_active", read_only=True)
    organisation_type_display = serializers.CharField(
        source="get_organisation_type_display", read_only=True
    )
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = InstitutionProfile
        fields = [
            "id",
            "name",
            "registration_number",
            "sector",
            "sector_other",
            "organisation_name",
            "organisation_type",
            "organisation_type_display",
            "organisation_type_other",
            "status",
            "status_display",
            "email",
            "phone",
            "contact_name",
            "email_verified",
            "is_active",
            "created_at",
            "updated_at",
        ]


class AdminInstitutionDocumentSerializer(serializers.ModelSerializer):
    file_name = serializers.SerializerMethodField()
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = InstitutionVerificationDocument
        fields = [
            "id",
            "file_name",
            "document_type",
            "file_url",
            "issuing_authority",
            "uploaded_at",
        ]
        read_only_fields = fields

    def get_file_name(self, document):
        return Path(document.file.name).name

    def get_file_url(self, document):
        request = self.context.get("request")
        url = reverse(
            "administration:admin-institution-document-download",
            kwargs={
                "pk": document.institution_id,
                "document_pk": document.pk,
            },
        )
        return request.build_absolute_uri(url) if request else url


class AdminInstitutionReviewSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source="organisation_name", read_only=True)
    registration_number = serializers.SerializerMethodField()
    sector = serializers.CharField(source="organisation_type", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    phone = serializers.CharField(source="user.phone_number", read_only=True)
    contact_person_name = serializers.CharField(
        source="user.full_name", read_only=True
    )
    address = serializers.SerializerMethodField()
    website = serializers.SerializerMethodField()
    founded_date = serializers.SerializerMethodField()
    last_active = serializers.DateTimeField(
        source="user.last_login", read_only=True, allow_null=True
    )
    status = serializers.SerializerMethodField()
    documents = AdminInstitutionDocumentSerializer(
        many=True, source="verification_documents", read_only=True
    )
    verification_progress = serializers.SerializerMethodField()

    class Meta:
        model = InstitutionProfile
        fields = [
            "id",
            "name",
            "registration_number",
            "sector",
            "email",
            "phone",
            "contact_person_name",
            "address",
            "website",
            "description",
            "founded_date",
            "last_active",
            "status",
            "created_at",
            "documents",
            "verification_progress",
        ]
        read_only_fields = fields

    def get_registration_number(self, institution):
        return None

    def get_address(self, institution):
        return None

    def get_website(self, institution):
        return None

    def get_founded_date(self, institution):
        return None

    def get_status(self, institution):
        if institution.status == InstitutionStatus.REJECTED:
            return "rejected"
        if institution.status == InstitutionStatus.DELETED or not institution.user.is_active:
            return "deactivated"
        return institution.status.lower()

    def get_verification_progress(self, institution):
        details = (
            institution.organisation_name,
            institution.organisation_type,
            institution.user.full_name,
            institution.user.email,
            institution.user.phone_number,
        )
        details_present = any(str(value or "").strip() for value in details)
        details_complete = all(str(value or "").strip() for value in details)
        detail_status = (
            "completed"
            if details_complete
            else "in_progress"
            if details_present
            else "not_started"
        )

        has_documents = institution.verification_documents.exists()
        document_status = "completed" if has_documents else "not_started"
        if institution.user.email_verified:
            checks_status = "completed"
        elif details_present or has_documents:
            checks_status = "in_progress"
        else:
            checks_status = "not_started"

        if institution.status != InstitutionStatus.PENDING:
            decision_status = "completed"
        elif details_present or has_documents:
            decision_status = "in_progress"
        else:
            decision_status = "not_started"

        return {
            "institution_details": detail_status,
            "documents": document_status,
            "verification_checks": checks_status,
            "approval_decision": decision_status,
        }
