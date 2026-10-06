from django.contrib.auth import get_user_model
from django.db import connection
from django.db.utils import OperationalError
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.exceptions import NotFound
from rest_framework.generics import RetrieveAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenRefreshView

from apps.core.permissions import ROLE_PERMISSIONS

from .serializers import LoginSerializer, TokenPairSerializer, UserSerializer

User = get_user_model()

HealthSerializer = inline_serializer(
    "Health",
    {"status": serializers.CharField(), "database": serializers.CharField()},
)

RoleOptionSerializer = inline_serializer(
    "RoleOption",
    {"value": serializers.CharField(), "label": serializers.CharField()},
    many=True,
)

RoleAccessSerializer = inline_serializer(
    "RoleAccess",
    {"role": serializers.CharField(), "detail": serializers.CharField()},
)


@extend_schema(
    tags=["Health"],
    summary="Health check",
    responses={200: HealthSerializer, 503: HealthSerializer},
)
@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    """Confirms the API is up and can reach the database."""
    try:
        connection.ensure_connection()
    except OperationalError:
        return Response({"status": "error", "database": "unavailable"}, status=503)
    return Response({"status": "ok", "database": "ok"})


@extend_schema(
    tags=["Auth"],
    summary="Roles a new user can choose",
    responses={200: RoleOptionSerializer},
)
@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def roles(request):
    """The account types shown on the role selection screen. The chosen `value` decides which
    registration form the user is sent to. Admin accounts are created by staff only, so they are not listed."""
    return Response([{"value": role.value, "label": role.label} for role in User.SELF_REGISTRATION_ROLES])


class LoginView(APIView):
    """Sign in with phone number or email plus password/PIN. Returns JWT tokens.

    Send the access token on later requests as: `Authorization: Bearer <access>`.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "login"

    @extend_schema(
        tags=["Auth"],
        summary="Sign in (phone or email)",
        request=LoginSerializer,
        responses={200: TokenPairSerializer},
        examples=[
            OpenApiExample("With phone", value={"identifier": "0772123456", "password": "your-password"},
                           request_only=True),
            OpenApiExample("With email", value={"identifier": "sarah@example.com", "password": "your-password"},
                           request_only=True),
        ],
    )
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(TokenPairSerializer.for_user(serializer.validated_data["user"]), status=status.HTTP_200_OK)


@extend_schema(tags=["Auth"], summary="Get a new access token from a refresh token")
class RefreshView(TokenRefreshView):
    pass


@extend_schema(tags=["Auth"], summary="Current signed-in user")
class MeView(RetrieveAPIView):
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user


class RoleAccessView(APIView):
    """Checks whether the signed-in user may use endpoints limited to `role`.

    It runs the same permission class that protects those endpoints (apps/core/permissions.py), so
    clients can confirm access before routing, and the 401/403 rules can be tried in Swagger.
    """

    def get_permissions(self):
        if getattr(self, "swagger_fake_view", False):  # schema generation has no URL kwargs
            return [IsAuthenticated()]
        permission_class = ROLE_PERMISSIONS.get(self.kwargs["role"])
        if permission_class is None:
            raise NotFound("Unknown role.")
        return [permission_class()]

    @extend_schema(
        tags=["Auth"],
        summary="Check access to a role's endpoints",
        parameters=[OpenApiParameter("role", OpenApiTypes.STR, OpenApiParameter.PATH, enum=User.Role.values)],
        responses={
            200: RoleAccessSerializer,
            401: OpenApiResponse(description="Not signed in."),
            403: OpenApiResponse(description="Signed in with a different role."),
            404: OpenApiResponse(description="Unknown role."),
        },
    )
    def get(self, request, role):
        return Response({"role": role, "detail": "Access granted."})
