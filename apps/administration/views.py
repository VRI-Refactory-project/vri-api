from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.institutions.models import InstitutionProfile

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
