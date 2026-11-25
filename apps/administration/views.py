from pathlib import Path

from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, status, serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts import recovery
from apps.audit import services as audit
from apps.audit.models import AuditLog
from apps.institutions.models import (
    InstitutionProfile,
    InstitutionStaff,
    InstitutionVerificationDocument,
    canonical_organisation_type,
)
from apps.institutions.serializers import InstitutionProfileUpdateSerializer

from .models import AdminProfile
from .serializers import (
    AdminChangePasswordSerializer,
    AdminInstitutionReviewSerializer,
    AdminInstitutionSerializer,
    AdminProfileSerializer,
)


class IsAdminUserPermission(permissions.BasePermission):
    """Ensure the user is authenticated and has an admin role or is staff/superuser."""

    def has_permission(self, request, view):
        user_role = getattr(request.user, "role", None)
        return bool(
            request.user
            and request.user.is_authenticated
            and (
                user_role in ["ADMINISTRATOR", "SYSTEM_ADMIN", "ADMIN", "admin"]
                or request.user.is_staff
                or request.user.is_superuser
            )
        )


@extend_schema(
    tags=["Administration Settings"],
    summary="Retrieve or update administrator profile and settings",
    description="View and update admin details, notification preferences, and contact info (V2-052).",
)
class AdminProfileDetailView(generics.RetrieveUpdateAPIView):
    permission_classes = [IsAdminUserPermission]
    serializer_class = AdminProfileSerializer

    def get_object(self):
        profile, _ = AdminProfile.objects.get_or_create(user=self.request.user)
        return profile


@extend_schema(
    tags=["Administration Settings"],
    summary="Change administrator password",
    description="Update admin password and record the password_last_changed timestamp (V2-052).",
    request=AdminChangePasswordSerializer,
)
class AdminChangePasswordView(APIView):
    permission_classes = [IsAdminUserPermission]

    def post(self, request, *args, **kwargs):
        serializer = AdminChangePasswordSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(
            {"detail": "Password updated successfully."},
            status=status.HTTP_200_OK,
        )


@extend_schema(
    tags=["Administration Institutions"],
    summary="List all institutions (admin only)",
)
class AdminInstitutionListView(generics.ListAPIView):
    permission_classes = [IsAdminUserPermission]
    serializer_class = AdminInstitutionSerializer
    queryset = InstitutionProfile.objects.select_related("user").order_by("-created_at")


@extend_schema(
    tags=["Administration Institutions"],
    summary="Retrieve or edit an institution",
)
class AdminInstitutionDetailView(generics.RetrieveUpdateAPIView):
    permission_classes = [IsAdminUserPermission]
    queryset = InstitutionProfile.objects.select_related("user").prefetch_related(
        "verification_documents"
    )
    serializer_class = InstitutionProfileUpdateSerializer
    http_method_names = ["get", "patch", "delete", "head", "options"]

    def get_serializer(self, *args, **kwargs):
        if self.request.method == "PATCH":
            kwargs["context"] = self.get_serializer_context()
            return AdminInstitutionEditSerializer(*args, **kwargs)
        if self.request.method == "GET":
            kwargs["context"] = self.get_serializer_context()
            return AdminInstitutionReviewSerializer(*args, **kwargs)
        return super().get_serializer(*args, **kwargs)

    def patch(self, request, *args, **kwargs):
        response = super().patch(request, *args, **kwargs)
        institution = self.get_object()
        return Response(
            AdminInstitutionSerializer(institution).data,
            status=response.status_code,
        )

    def delete(self, request, *args, **kwargs):
        institution = self.get_object()
        institution.user.delete()
        return Response({"detail": "Institution deleted successfully."}, status=status.HTTP_200_OK)


class AdminInstitutionEditSerializer(serializers.ModelSerializer):
    """Admin-facing institution fields, including the linked account contact details."""
    name = serializers.CharField(source="organisation_name", required=False, max_length=200)
    organisation_name = serializers.CharField(
        required=False, max_length=200, write_only=True
    )
    registration_number = serializers.CharField(required=False, allow_blank=True, max_length=100)
    sector = serializers.ChoiceField(
        source="organisation_type",
        choices=InstitutionProfile._meta.get_field("organisation_type").choices,
        required=False,
    )
    organisation_type = serializers.ChoiceField(
        choices=InstitutionProfile._meta.get_field("organisation_type").choices,
        required=False,
        write_only=True,
    )
    sector_other = serializers.CharField(
        source="organisation_type_other",
        required=False,
        allow_blank=True,
        max_length=100,
    )
    organisation_type_other = serializers.CharField(
        required=False, allow_blank=True,
        max_length=100, write_only=True,
    )
    email = serializers.EmailField(
        source="user.email", required=False, allow_null=True, allow_blank=True
    )
    phone = serializers.CharField(source="user.phone_number", required=False)

    class Meta:
        model = InstitutionProfile
        fields = (
            "name", "organisation_name", "registration_number", "sector", "sector_other",
            "organisation_type", "organisation_type_other", "email", "phone",
        )

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Institution name cannot be blank.")
        return value

    def validate_email(self, value):
        value = (value or "").strip().lower()
        qs = get_user_model().objects.filter(email__iexact=value).exclude(pk=self.instance.user_id)
        if value and qs.exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value or None

    def validate_phone(self, value):
        from apps.accounts.serializers import PlusPhoneNumberField
        value = PlusPhoneNumberField().run_validation(value)
        if get_user_model().objects.filter(phone_number=value).exclude(pk=self.instance.user_id).exists():
            raise serializers.ValidationError("An account with this phone number already exists.")
        return value

    def validate(self, attrs):
        sector = canonical_organisation_type(
            attrs.get("organisation_type", self.instance.organisation_type)
        )
        other = attrs.get("organisation_type_other", self.instance.organisation_type_other)
        other_changed = "organisation_type_other" in attrs
        sector_changed = "organisation_type" in attrs
        if sector == "OTHER":
            other = (other or "").strip()
            if (sector_changed or other_changed) and not other:
                raise serializers.ValidationError(
                    {"sector_other": "Specify the institution sector."}
                )
            if other_changed:
                attrs["organisation_type_other"] = other
        if sector != "OTHER" and (
            sector_changed or other_changed
        ):
            attrs["organisation_type_other"] = ""
        return attrs

    def update(self, instance, validated_data):
        user_data = validated_data.pop("user", {})
        if user_data.get("email", instance.user.email) != instance.user.email:
            instance.user.email_verified_at = None
            user_data["email_verified_at"] = None
        for field, value in validated_data.items():
            target = "registration_number" if field == "registration_number" else field
            setattr(instance, target, value)
        instance.organisation_type = canonical_organisation_type(
            instance.organisation_type
        )
        for field, value in user_data.items():
            setattr(instance.user, field, value)
        instance.save()
        if user_data:
            instance.user.save(update_fields=list(user_data))
        return instance


class AdminInstitutionDocumentDownloadView(APIView):
    permission_classes = [IsAdminUserPermission]

    def get(self, request, pk, document_pk):
        document = get_object_or_404(
            InstitutionVerificationDocument.objects.select_related("institution"),
            pk=document_pk,
            institution_id=pk,
        )
        response = FileResponse(
            document.file.open("rb"),
            as_attachment=True,
            filename=Path(document.file.name).name,
            content_type="application/octet-stream",
        )
        response["X-Content-Type-Options"] = "nosniff"
        return response


@extend_schema(
    tags=["Administration Institutions"],
    summary="Deactivate an institution and revoke all staff access",
    request=None,
    responses={200: AdminInstitutionSerializer},
)
class AdminInstitutionDeactivateView(APIView):
    permission_classes = [IsAdminUserPermission]

    def post(self, request, pk):
        with transaction.atomic():
            institution = get_object_or_404(
                InstitutionProfile.objects.select_for_update().select_related("user"),
                pk=pk,
            )
            now = timezone.now()
            memberships = list(
                InstitutionStaff.objects.select_for_update()
                .filter(institution=institution)
                .select_related("user")
            )
            users = {membership.user_id: membership.user for membership in memberships}
            users[institution.user_id] = institution.user

            for membership in memberships:
                if membership.revoked_at is None:
                    membership.revoked_at = now
                    membership.revoked_by = request.user
                    membership.save(
                        update_fields=["revoked_at", "revoked_by", "updated_at"]
                    )

            for user in users.values():
                if user.is_active:
                    user.is_active = False
                    user.save(update_fields=["is_active"])
                recovery.sign_out_everywhere(user)

            audit.record(
                AuditLog.Event.INSTITUTION_DEACTIVATED,
                request.user,
                request,
                target_user=institution.user,
                details={"institution_id": institution.pk},
            )

        return Response(AdminInstitutionSerializer(institution).data)
