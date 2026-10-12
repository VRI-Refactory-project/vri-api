"""Dashboard data for each role (V2-062 Deaf user, V2-064 administrator).

Each view is limited to its role with the shared permission classes (apps/core/permissions.py):
not signed in -> 401, another role -> 403.

Sessions, requests and chats do not exist in the API yet, so those lists are empty for now. They are
already in the response so the web and mobile screens can be built against the final shape.
"""
from django.contrib.auth import get_user_model
from django.db.models import Count
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.serializers import UserSerializer
from apps.core.permissions import IsAdminRole, IsDeafUser
from apps.institutions.models import InstitutionProfile, InstitutionStatus
from apps.interpreters.models import InterpreterProfile, VerificationStatus

User = get_user_model()

DeafDashboardSerializer = inline_serializer(
    "DeafDashboard",
    {
        "user": UserSerializer(),
        "requests": serializers.ListField(child=serializers.DictField()),
        "sessions": serializers.ListField(child=serializers.DictField()),
        "chats": serializers.ListField(child=serializers.DictField()),
    },
)

AdminDashboardSerializer = inline_serializer(
    "AdminDashboard",
    {
        "user": UserSerializer(),
        "interpreters_pending_verification": serializers.IntegerField(),
        "institutions_pending_approval": serializers.IntegerField(),
        "users_by_role": serializers.DictField(child=serializers.IntegerField()),
    },
)


class DeafDashboardView(APIView):
    """Everything the Deaf user's home screen needs in one request."""

    permission_classes = [IsDeafUser]

    @extend_schema(
        tags=["Dashboards"],
        summary="Deaf user dashboard",
        responses={200: DeafDashboardSerializer},
    )
    def get(self, request):
        return Response(
            {
                "user": UserSerializer(request.user).data,
                "requests": [],
                "sessions": [],
                "chats": [],
            }
        )


class AdminDashboardView(APIView):
    """Summary numbers for the administrator's home screen."""

    permission_classes = [IsAdminRole]

    @extend_schema(
        tags=["Dashboards"],
        summary="Administrator dashboard",
        responses={200: AdminDashboardSerializer},
    )
    def get(self, request):
        counts = dict(
            User.objects.filter(is_active=True).values_list("role").annotate(total=Count("id"))
        )
        return Response(
            {
                "user": UserSerializer(request.user).data,
                "interpreters_pending_verification": InterpreterProfile.objects.filter(
                    verification_status=VerificationStatus.INACTIVE
                ).count(),
                "institutions_pending_approval": InstitutionProfile.objects.filter(
                    status=InstitutionStatus.PENDING
                ).count(),
                "users_by_role": {role: counts.get(role, 0) for role in User.Role.values},
            }
        )
