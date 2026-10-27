from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts import recovery
from apps.audit import services as audit
from apps.audit.models import AuditLog
from apps.institutions.models import InstitutionProfile, InstitutionStaff
from apps.institutions.serializers import InstitutionProfileUpdateSerializer

from .models import AdminProfile
from .serializers import (
    AdminChangePasswordSerializer,
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
                user_role in ["ADMINISTRATOR", "SYSTEM_ADMIN", "ADMIN"]
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
    queryset = InstitutionProfile.objects.select_related("user")
    serializer_class = InstitutionProfileUpdateSerializer
    http_method_names = ["get", "patch", "head", "options"]

    def get_serializer(self, *args, **kwargs):
        if self.request.method == "GET":
            kwargs["context"] = self.get_serializer_context()
            return AdminInstitutionSerializer(*args, **kwargs)
        return super().get_serializer(*args, **kwargs)

    def patch(self, request, *args, **kwargs):
        response = super().patch(request, *args, **kwargs)
        institution = self.get_object()
        return Response(
            AdminInstitutionSerializer(institution).data,
            status=response.status_code,
        )


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
