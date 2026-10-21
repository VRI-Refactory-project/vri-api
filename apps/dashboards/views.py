"""Dashboard data for each role.

Deaf, interpreter, and administrator dashboard endpoints are restricted
by shared permission classes in apps/core/permissions.py.
"""

from django.contrib.auth import get_user_model
from django.db.models import Count
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.serializers import UserSerializer
from apps.audit.models import AuditLog
from apps.core.permissions import (
    CanAccessInterpreterDashboard,
    IsAdminRole,
    IsDeafUser,
)
from apps.institutions.models import InstitutionProfile, InstitutionStatus
from apps.interpreters.models import InterpreterProfile, Specialisation, VerificationStatus
from apps.interpreters.services import get_eligible_interpreters
from apps.users.photos import photo_path

User = get_user_model()

RECENT_SIGN_INS = 10
AVAILABLE_INTERPRETERS_LIMIT = 12

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
        "recent_admin_sign_ins": inline_serializer(
            "AdminSignIn",
            {
                "id": serializers.IntegerField(),
                "admin": serializers.CharField(),
                "ip_address": serializers.CharField(allow_null=True),
                "at": serializers.DateTimeField(),
            },
            many=True,
        ),
    },
)

InterpreterDashboardSerializer = inline_serializer(
    "InterpreterDashboard",
    {
        "user": UserSerializer(),
        "verification_status": serializers.CharField(),
        "is_available": serializers.BooleanField(),
        "specialisation": serializers.CharField(allow_null=True),
        "working_hours_schedule": serializers.DictField(),
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


def interpreter_card(profile):
    """The public details of an interpreter that a Deaf user may see. No email or phone number."""
    specialisation = (
        profile.specialisation_other
        if profile.specialisation == Specialisation.OTHER and profile.specialisation_other
        else profile.get_specialisation_display()
    )
    return {
        "id": profile.pk,
        "name": profile.user.full_name,
        "sign_languages": profile.sign_languages,
        "specialisation": specialisation,
        "photo_url": photo_path(profile.user),
    }


AVAILABLE_INTERPRETER_FIELDS = {
    "id": serializers.IntegerField(),
    "name": serializers.CharField(),
    "sign_languages": serializers.ListField(child=serializers.CharField()),
    "specialisation": serializers.CharField(),
    "photo_url": serializers.CharField(allow_null=True),
}
AvailableInterpreterListSerializer = inline_serializer(
    "AvailableInterpreter", AVAILABLE_INTERPRETER_FIELDS, many=True
)
InterpreterPublicProfileSerializer = inline_serializer(
    "InterpreterPublicProfile",
    {**AVAILABLE_INTERPRETER_FIELDS, "available_now": serializers.BooleanField()},
)


class AvailableInterpretersView(APIView):
    """Interpreters a Deaf user could be connected to right now (the dashboard's list).

    Uses the same rule as routing, so the list matches who can actually be offered a request.
    Only public details are returned: no email or phone number.
    """

    permission_classes = [IsDeafUser]

    @extend_schema(
        tags=["Dashboards"],
        summary="Interpreters available now",
        responses={200: AvailableInterpreterListSerializer},
    )
    def get(self, request):
        profiles = (
            get_eligible_interpreters()
            .select_related("user__photo")
            .defer("user__photo__image")
            .order_by("user__full_name")[:AVAILABLE_INTERPRETERS_LIMIT]
        )
        return Response([interpreter_card(profile) for profile in profiles])


class InterpreterPublicProfileView(APIView):
    """One interpreter's public profile, for the "View profile" button on the dashboard.

    Any approved, active interpreter can be opened, whether or not they are available right now.
    """

    permission_classes = [IsDeafUser]

    @extend_schema(
        tags=["Dashboards"],
        summary="An interpreter's public profile",
        responses={200: InterpreterPublicProfileSerializer},
    )
    def get(self, request, pk):
        profile = get_object_or_404(
            InterpreterProfile.objects.select_related("user__photo").defer("user__photo__image"),
            pk=pk,
            verification_status=VerificationStatus.APPROVED,
            user__is_active=True,
        )
        data = interpreter_card(profile)
        data["available_now"] = get_eligible_interpreters().filter(pk=profile.pk).exists()
        return Response(data)


class AdminDashboardView(APIView):
    """Summary numbers and latest administrator sign-ins."""

    permission_classes = [IsAdminRole]

    @extend_schema(
        tags=["Dashboards"],
        summary="Administrator dashboard",
        responses={200: AdminDashboardSerializer},
    )
    def get(self, request):
        counts = dict(
            User.objects.filter(is_active=True)
            .values_list("role")
            .annotate(total=Count("id"))
        )

        return Response(
            {
                "user": UserSerializer(request.user).data,
                "interpreters_pending_verification": (
                    InterpreterProfile.objects.filter(
                        verification_status=VerificationStatus.INACTIVE
                    ).count()
                ),
                "institutions_pending_approval": (
                    InstitutionProfile.objects.filter(
                        status=InstitutionStatus.PENDING
                    ).count()
                ),
                "users_by_role": {
                    role: counts.get(role, 0) for role in User.Role.values
                },
                "recent_admin_sign_ins": [
                    {
                        "id": entry.id,
                        "admin": (entry.user.full_name if entry.user else None),
                        "ip_address": entry.ip_address,
                        "at": entry.created_at,
                    }
                    for entry in (
                        AuditLog.objects.filter(
                            event=AuditLog.Event.ADMIN_SIGN_IN
                        ).select_related("user")[:RECENT_SIGN_INS]
                    )
                ],
            }
        )


class InterpreterDashboardView(APIView):
    """Dashboard data for active, approved interpreters."""

    permission_classes = [CanAccessInterpreterDashboard]

    @extend_schema(
        tags=["Dashboards"],
        summary="Interpreter dashboard",
        responses={200: InterpreterDashboardSerializer},
    )
    def get(self, request):
        profile = InterpreterProfile.objects.get(user=request.user)

        return Response(
            {
                "user": UserSerializer(request.user).data,
                "verification_status": profile.verification_status,
                "is_available": profile.is_available,
                "specialisation": profile.specialisation,
                "working_hours_schedule": profile.working_hours_schedule,
            }
        )
