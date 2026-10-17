import logging

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import ValidationError
from rest_framework.generics import RetrieveAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit import services as audit
from apps.audit.models import AuditLog
from apps.core.permissions import (
    HasInstitutionalRole,
    IsAdminRole,
    IsApprovedInstitution,
    IsInstitutionAdmin,
    user_institution,
)

from . import staff
from .models import InstitutionProfile, InstitutionStaff, InstitutionStatus
from .serializers import (
    InstitutionProfileUpdateSerializer,
    InstitutionSerializer,
    StaffCreateSerializer,
    StaffSerializer,
    StaffUpdateSerializer,
)

logger = logging.getLogger(__name__)


class InstitutionApplicationSerializer(serializers.ModelSerializer):
    administrator_name = serializers.CharField(source="user.full_name", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    phone_number = serializers.CharField(source="user.phone_number", read_only=True)

    class Meta:
        model = InstitutionProfile
        fields = [
            "id",
            "organisation_name",
            "organisation_type",
            "organisation_type_other",
            "status",
            "administrator_name",
            "email",
            "phone_number",
            "created_at",
        ]


def notify_institution(profile, decision):
    """Attempt to email the institution administrator about the decision."""
    if not profile.user.email:
        return

    approved = decision == InstitutionStatus.APPROVED
    subject = (
        "Institution account approved"
        if approved
        else "Institution account application rejected"
    )
    message = (
        f"Hello {profile.user.full_name},\n\n"
        f"Your application for {profile.organisation_name} "
        f"has been {'approved' if approved else 'rejected'}.\n"
    )
    try:
        send_mail(
            subject,
            message,
            getattr(settings, "DEFAULT_FROM_EMAIL", None),
            [profile.user.email],
            fail_silently=False,
        )
    except Exception:
        logger.exception(
            "Could not send institution decision notification; institution_id=%s",
            profile.pk,
        )


class PendingInstitutionListView(APIView):
    permission_classes = [IsAdminRole]

    @extend_schema(
        tags=["Institutions"],
        summary="List institution applications awaiting approval",
        responses={200: InstitutionApplicationSerializer(many=True)},
    )
    def get(self, request):
        applications = InstitutionProfile.objects.filter(
            status=InstitutionStatus.PENDING
        ).select_related("user")
        return Response(InstitutionApplicationSerializer(applications, many=True).data)


class InstitutionDecisionView(APIView):
    permission_classes = [IsAdminRole]
    decision = None
    audit_event = None

    def post(self, request, pk):
        with transaction.atomic():
            profile = get_object_or_404(
                InstitutionProfile.objects.select_for_update().select_related("user"),
                pk=pk,
            )

            if profile.status != InstitutionStatus.PENDING:
                raise ValidationError(
                    {
                        "status": [
                            "Only pending institution applications can be approved or rejected."
                        ]
                    }
                )

            profile.status = self.decision
            profile.updated_at = timezone.now()
            profile.save(update_fields=["status", "updated_at"])

            audit.record(self.audit_event, request.user, request)

            transaction.on_commit(lambda: notify_institution(profile, self.decision))

        return Response(
            InstitutionApplicationSerializer(profile).data,
            status=status.HTTP_200_OK,
        )


class ApproveInstitutionView(InstitutionDecisionView):
    decision = InstitutionStatus.APPROVED
    audit_event = AuditLog.Event.INSTITUTION_APPROVED

    @extend_schema(
        tags=["Institutions"],
        summary="Approve a pending institution application",
        request=None,
        responses={200: InstitutionApplicationSerializer},
    )
    def post(self, request, pk):
        return super().post(request, pk)


class RejectInstitutionView(InstitutionDecisionView):
    decision = InstitutionStatus.REJECTED
    audit_event = AuditLog.Event.INSTITUTION_REJECTED

    @extend_schema(
        tags=["Institutions"],
        summary="Reject a pending institution application",
        request=None,
        responses={200: InstitutionApplicationSerializer},
    )
    def post(self, request, pk):
        return super().post(request, pk)


# --- V2-006: staff accounts (institution administrators only) & My Institution ---


@extend_schema(
    tags=["Institutions"],
    summary="My institution",
    responses={
        200: InstitutionSerializer,
        403: OpenApiResponse(
            description="Not an institution, or the institution is pending or rejected "
            "(code: institution_pending / institution_rejected)."
        ),
    },
)
class MyInstitutionView(RetrieveAPIView):
    """The signed-in user's own institution. There is no way to ask for another institution by id."""

    permission_classes = [IsApprovedInstitution]
    serializer_class = InstitutionSerializer

    def get_object(self):
        return user_institution(self.request.user)


class InstitutionalAccountUpdateAPIView(APIView):
    """
    Endpoint for institutional managers to update their organization's account details.
    """

    permission_classes = [IsAuthenticated, HasInstitutionalRole]

    def get_object(self, request):
        return get_object_or_404(
            InstitutionProfile.objects.filter(
                user=request.user,
                status=InstitutionStatus.APPROVED,
            )
        )

    def get(self, request, *args, **kwargs):
        """Retrieve the current account settings profile configuration."""
        institution = self.get_object(request)
        serializer = InstitutionProfileUpdateSerializer(institution)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def patch(self, request, *args, **kwargs):
        """Partially update account configuration properties dynamically."""
        institution = self.get_object(request)

        # Pass partial=True so the frontend can send only the fields that changed
        serializer = InstitutionProfileUpdateSerializer(
            institution, data=request.data, partial=True
        )

        if serializer.is_valid():
            serializer.save()
            return Response(
                {
                    "message": "Institutional settings updated successfully.",
                    "updated_data": serializer.data,
                },
                status=status.HTTP_200_OK,
            )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class InstitutionalDashboardView(APIView):
    permission_classes = [IsAuthenticated, HasInstitutionalRole]

    def get(self, request):
        return Response({"message": "Institutional access granted."})


STAFF_ERRORS = {
    403: OpenApiResponse(
        description="Not an administrator of an approved institution "
        "(code: institution_admin_required / institution_pending / ...)."
    ),
    404: OpenApiResponse(
        description="No staff member with this id in your institution."
    ),
}


class InstitutionStaffMixin:
    """Every staff endpoint works only on the caller's own institution, never on an id from the request."""

    permission_classes = [IsInstitutionAdmin]

    def staff_queryset(self):
        return InstitutionStaff.objects.filter(
            institution=user_institution(self.request.user)
        ).select_related("user")

    def get_membership(self, pk):
        return get_object_or_404(self.staff_queryset(), pk=pk)


class StaffListCreateView(InstitutionStaffMixin, APIView):
    """List your institution's staff, or add a staff member (their login details are emailed to them)."""

    @extend_schema(
        tags=["Institution Staff"],
        summary="List my institution's staff",
        parameters=[
            OpenApiParameter(
                "status",
                OpenApiTypes.STR,
                enum=["ACTIVE", "REVOKED"],
                description="Only active or only revoked staff.",
            )
        ],
        responses={200: StaffSerializer(many=True), **STAFF_ERRORS},
    )
    def get(self, request):
        queryset = self.staff_queryset()
        status_filter = request.query_params.get("status")
        if status_filter == "ACTIVE":
            queryset = queryset.filter(revoked_at__isnull=True)
        elif status_filter == "REVOKED":
            queryset = queryset.filter(revoked_at__isnull=False)
        elif status_filter:
            raise ValidationError({"status": ["Choose ACTIVE or REVOKED."]})
        return Response(StaffSerializer(queryset, many=True).data)

    @extend_schema(
        tags=["Institution Staff"],
        summary="Add a staff member (login details are emailed to them)",
        request=StaffCreateSerializer,
        responses={
            201: StaffSerializer,
            400: OpenApiResponse(
                description="Invalid or duplicate details (same rules as registration)."
            ),
            503: OpenApiResponse(
                description="The login-details email could not be sent; nothing was created."
            ),
            **STAFF_ERRORS,
        },
    )
    def post(self, request):
        serializer = StaffCreateSerializer(
            data=request.data,
            context={"request": request, "institution": user_institution(request.user)},
        )
        serializer.is_valid(raise_exception=True)
        try:
            membership = staff.create_staff(serializer, request.user, request)
        except staff.StaffEmailError:
            return Response(
                {
                    "detail": "The login details could not be emailed, so the staff account was not created. "
                    "Please try again later."
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(
            StaffSerializer(membership).data, status=status.HTTP_201_CREATED
        )


class StaffDetailView(InstitutionStaffMixin, APIView):
    """View or change one staff member: name, job title and role (ADMIN or STAFF)."""

    @extend_schema(
        tags=["Institution Staff"],
        summary="Staff member details",
        responses={200: StaffSerializer, **STAFF_ERRORS},
    )
    def get(self, request, pk):
        return Response(StaffSerializer(self.get_membership(pk)).data)

    @extend_schema(
        tags=["Institution Staff"],
        summary="Change a staff member's name, job title or role",
        request=StaffUpdateSerializer,
        responses={
            200: StaffSerializer,
            400: OpenApiResponse(
                description="Invalid data, or it would leave no active administrator."
            ),
            **STAFF_ERRORS,
        },
    )
    def patch(self, request, pk):
        membership = self.get_membership(pk)
        serializer = StaffUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        staff.update_staff(membership, serializer.validated_data, request.user, request)
        return Response(StaffSerializer(membership).data)


class StaffRevokeView(InstitutionStaffMixin, APIView):
    """Remove a staff member's access at once. Their account and history are kept; they cannot sign in, and
    sessions already open stop working. Revoking someone already revoked changes nothing."""

    @extend_schema(
        tags=["Institution Staff"],
        summary="Revoke a staff member's access (takes effect immediately)",
        request=None,
        responses={
            200: StaffSerializer,
            400: OpenApiResponse(
                description="Your own access, or the last active administrator."
            ),
            **STAFF_ERRORS,
        },
    )
    def post(self, request, pk):
        membership = staff.revoke_staff(self.get_membership(pk), request.user, request)
        return Response(StaffSerializer(membership).data)


class StaffReinstateView(InstitutionStaffMixin, APIView):
    """Give a revoked staff member access again (same password and role as before)."""

    @extend_schema(
        tags=["Institution Staff"],
        summary="Reinstate a revoked staff member",
        request=None,
        responses={200: StaffSerializer, **STAFF_ERRORS},
    )
    def post(self, request, pk):
        membership = staff.reinstate_staff(
            self.get_membership(pk), request.user, request
        )
        return Response(StaffSerializer(membership).data)
