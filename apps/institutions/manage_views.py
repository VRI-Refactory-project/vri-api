"""V2-005: platform administrators create institution accounts (/api/v1/manage/institutions/)."""

from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.recovery import mask_email
from apps.accounts.serializers import UserSerializer
from apps.core.permissions import IsAdminRole

from . import credentials
from .serializers import AdminInstitutionCreateSerializer, InstitutionSerializer

InstitutionCreatedSerializer = inline_serializer(
    "InstitutionCreated",
    {"detail": serializers.CharField(), "institution": InstitutionSerializer(), "user": UserSerializer()},
)


class ManageInstitutionCreateView(APIView):
    """Create an institution that is APPROVED at once (no pending review).

    `full_name`, `phone_number` and `email` describe the institution's administrator. A temporary password is
    emailed to them (never shown here); they must choose their own at first sign-in. If the email cannot be
    sent, nothing is created.
    """

    permission_classes = [IsAdminRole]

    @extend_schema(
        tags=["Manage"],
        summary="Create an approved institution and email its login details",
        request=AdminInstitutionCreateSerializer,
        responses={
            201: InstitutionCreatedSerializer,
            400: OpenApiResponse(description="Invalid or duplicate details (same rules as self-registration)."),
            403: OpenApiResponse(description="Not a platform administrator."),
            503: OpenApiResponse(description="The login-details email could not be sent; nothing was created."),
        },
    )
    def post(self, request):
        try:
            user = credentials.create_approved_institution(request.data, request.user, request)
        except credentials.CredentialsEmailError:
            return Response(
                {"detail": "The login details could not be emailed, so the institution was not created. "
                           "Check the email settings and try again."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(
            {
                "detail": f"Institution created and approved. Login details sent to {mask_email(user.email)}.",
                "institution": InstitutionSerializer(user.institution_profile).data,
                "user": UserSerializer(user).data,
            },
            status=status.HTTP_201_CREATED,
        )
