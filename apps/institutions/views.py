from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.generics import RetrieveAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsApprovedInstitution, IsInstitutionAdmin, user_institution

from . import staff
from .models import InstitutionStaff
from .serializers import InstitutionSerializer, StaffCreateSerializer, StaffSerializer, StaffUpdateSerializer


@extend_schema(
    tags=["Institutions"],
    summary="My institution",
    responses={
        200: InstitutionSerializer,
        403: OpenApiResponse(description="Not an institution, or the institution is pending or rejected "
                                         "(code: institution_pending / institution_rejected)."),
    },
)
class MyInstitutionView(RetrieveAPIView):
    """The signed-in user's own institution. There is no way to ask for another institution by id."""

    permission_classes = [IsApprovedInstitution]
    serializer_class = InstitutionSerializer

    def get_object(self):
        return user_institution(self.request.user)


# --- V2-006: staff accounts (institution administrators only) ----------------------------------------


STAFF_ERRORS = {
    403: OpenApiResponse(description="Not an administrator of an approved institution "
                                     "(code: institution_admin_required / institution_pending / ...)."),
    404: OpenApiResponse(description="No staff member with this id in your institution."),
}


class InstitutionStaffMixin:
    """Every staff endpoint works only on the caller's own institution, never on an id from the request."""

    permission_classes = [IsInstitutionAdmin]

    def staff_queryset(self):
        return InstitutionStaff.objects.filter(institution=user_institution(self.request.user)).select_related("user")

    def get_membership(self, pk):
        return get_object_or_404(self.staff_queryset(), pk=pk)


class StaffListCreateView(InstitutionStaffMixin, APIView):
    """List your institution's staff, or add a staff member (their login details are emailed to them)."""

    @extend_schema(
        tags=["Institution Staff"],
        summary="List my institution's staff",
        parameters=[OpenApiParameter("status", OpenApiTypes.STR, enum=["ACTIVE", "REVOKED"],
                                     description="Only active or only revoked staff.")],
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
            400: OpenApiResponse(description="Invalid or duplicate details (same rules as registration)."),
            503: OpenApiResponse(description="The login-details email could not be sent; nothing was created."),
            **STAFF_ERRORS,
        },
    )
    def post(self, request):
        serializer = StaffCreateSerializer(
            data=request.data, context={"request": request, "institution": user_institution(request.user)},
        )
        serializer.is_valid(raise_exception=True)
        try:
            membership = staff.create_staff(serializer, request.user, request)
        except staff.StaffEmailError:
            return Response(
                {"detail": "The login details could not be emailed, so the staff account was not created. "
                           "Please try again later."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(StaffSerializer(membership).data, status=status.HTTP_201_CREATED)


class StaffDetailView(InstitutionStaffMixin, APIView):
    """View or change one staff member: name, job title and role (ADMIN or STAFF)."""

    @extend_schema(tags=["Institution Staff"], summary="Staff member details",
                   responses={200: StaffSerializer, **STAFF_ERRORS})
    def get(self, request, pk):
        return Response(StaffSerializer(self.get_membership(pk)).data)

    @extend_schema(
        tags=["Institution Staff"],
        summary="Change a staff member's name, job title or role",
        request=StaffUpdateSerializer,
        responses={200: StaffSerializer,
                   400: OpenApiResponse(description="Invalid data, or it would leave no active administrator."),
                   **STAFF_ERRORS},
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
        responses={200: StaffSerializer,
                   400: OpenApiResponse(description="Your own access, or the last active administrator."),
                   **STAFF_ERRORS},
    )
    def post(self, request, pk):
        membership = staff.revoke_staff(self.get_membership(pk), request.user, request)
        return Response(StaffSerializer(membership).data)


class StaffReinstateView(InstitutionStaffMixin, APIView):
    """Give a revoked staff member access again (same password and role as before)."""

    @extend_schema(tags=["Institution Staff"], summary="Reinstate a revoked staff member",
                   request=None, responses={200: StaffSerializer, **STAFF_ERRORS})
    def post(self, request, pk):
        membership = staff.reinstate_staff(self.get_membership(pk), request.user, request)
        return Response(StaffSerializer(membership).data)
