from django.contrib.auth import get_user_model
from django.contrib.auth.models import update_last_login
from django.db import connection
from django.db.utils import OperationalError
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_datetime
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    inline_serializer,
)
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.generics import RetrieveAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import BaseThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenRefreshView

from apps.audit import services as audit
from apps.audit.models import AuditLog
from apps.core.permissions import ROLE_PERMISSIONS, IsAdminRole, IsSignedOut
from apps.institutions.serializers import InstitutionRegistrationSerializer
from apps.interpreters.serializers import InterpreterRegistrationSerializer

from . import recovery
from .models import (
    AvailabilityOverride,
    AvailabilityPeriod,
    BaseAvailability,
    RecoveryCode,
)
from .serializers import (
    AdminResetConfirmSerializer,
    AvailabilityOverrideSerializer,
    AvailabilityPeriodSerializer,
    BaseAvailabilitySerializer,
    DeafRegistrationSerializer,
    EmailVerifyConfirmSerializer,
    LoginSerializer,
    PasswordChangeSerializer,
    RecoveryConfirmSerializer,
    RecoveryRequestSerializer,
    TokenPairSerializer,
    UserSerializer,
    check_new_password,
)
from .utils import find_user_by_identifier

User = get_user_model()
DetailSerializer = inline_serializer("Detail", {"detail": serializers.CharField()})
ADMIN_CODE_ERRORS = {
    "cooldown": "A code was sent less than a minute ago. Check your email or wait before trying again.",
    "limited": "Too many codes requested. Try again later.",
    "locked": "Too many wrong codes. Admin resets are locked for 24 hours.",
    "failed": "The email could not be sent. Check the email settings and try again.",
}
EMAIL_CODE_ERRORS = {
    "cooldown": "A code was sent less than a minute ago. Check your email or wait before trying again.",
    "limited": "Too many codes requested. Try again later.",
    "locked": "Too many wrong codes. Try again in 24 hours.",
    "failed": "We could not send the email. Please try again later.",
}
RECOVERY_REQUEST_MESSAGE = (
    "If an account matches, we have sent a recovery code to its registered email. "
    "If you registered with a phone number only, please contact support to reset your PIN."
)


def client_ip(request):
    # Same logic as the rate limiter: respects NUM_PROXIES (X-Forwarded-For) behind a proxy.
    return BaseThrottle().get_ident(request)


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
    return Response(
        [
            {"value": role.value, "label": role.label}
            for role in User.SELF_REGISTRATION_ROLES
        ]
    )


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
            OpenApiExample(
                "With phone",
                value={"identifier": "0772123456", "password": "your-password"},
                request_only=True,
            ),
            OpenApiExample(
                "With email",
                value={"identifier": "sarah@example.com", "password": "your-password"},
                request_only=True,
            ),
        ],
    )
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        update_last_login(
            None, user
        )  # when the user last signed in, shown in the admin site
        if user.role == User.Role.ADMIN:
            audit.record(AuditLog.Event.ADMIN_SIGN_IN, user, request)
        return Response(TokenPairSerializer.for_user(user), status=status.HTTP_200_OK)


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
        if getattr(
            self, "swagger_fake_view", False
        ):  # schema generation has no URL kwargs
            return [IsAuthenticated()]
        permission_class = ROLE_PERMISSIONS.get(self.kwargs["role"])
        if permission_class is None:
            raise NotFound("Unknown role.")
        return [permission_class()]

    @extend_schema(
        tags=["Auth"],
        summary="Check access to a role's endpoints",
        parameters=[
            OpenApiParameter(
                "role", OpenApiTypes.STR, OpenApiParameter.PATH, enum=User.Role.values
            )
        ],
        responses={
            200: RoleAccessSerializer,
            401: OpenApiResponse(description="Not signed in."),
            403: OpenApiResponse(description="Signed in with a different role."),
            404: OpenApiResponse(description="Unknown role."),
        },
    )
    def get(self, request, role):
        return Response({"role": role, "detail": "Access granted."})


# --- Registration (V2-001 deaf user, V2-003 interpreter) -----------------------------------------


class BaseRegisterView(APIView):
    """Creates an account for one role and signs the user in (returns the same payload as login).

    Only for people who are signed out (IsSignedOut).
    """

    permission_classes = [IsSignedOut]
    authentication_classes = []
    throttle_scope = "register"
    serializer_class = None

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        if user.email:
            # Best effort: if the mail fails the account still exists and the user can ask for a new code.
            recovery.issue_code(
                user, RecoveryCode.Purpose.EMAIL_VERIFY, ip=client_ip(request)
            )
        return Response(
            TokenPairSerializer.for_user(user), status=status.HTTP_201_CREATED
        )


@extend_schema(
    tags=["Auth"],
    summary="Register a Deaf / Hard of Hearing user",
    request=DeafRegistrationSerializer,
    responses={201: TokenPairSerializer},
)
class DeafRegisterView(BaseRegisterView):
    serializer_class = DeafRegistrationSerializer


@extend_schema(
    tags=["Auth"],
    summary="Register an interpreter (account starts pending review)",
    request=InterpreterRegistrationSerializer,
    responses={201: TokenPairSerializer},
)
class InterpreterRegisterView(BaseRegisterView):
    serializer_class = InterpreterRegistrationSerializer


@extend_schema(
    tags=["Auth"],
    summary="Register an institution (the registrant becomes its administrator; pending approval)",
    request=InstitutionRegistrationSerializer,
    responses={201: TokenPairSerializer},
)
class InstitutionRegisterView(BaseRegisterView):
    serializer_class = InstitutionRegistrationSerializer


# --- V2-008: account recovery ---------------------------------------------------------------


class RecoveryRequestView(APIView):
    """Step 1: ask for a recovery code by email.

    Always returns the same 200 response, whether or not the account exists, so this endpoint
    cannot be used to find out who is registered.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "recovery_request"

    @extend_schema(
        tags=["Auth"],
        summary="Account recovery: request a code by email",
        request=RecoveryRequestSerializer,
        responses={200: DetailSerializer},
    )
    def post(self, request):
        serializer = RecoveryRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = find_user_by_identifier(serializer.validated_data["identifier"])
        if user is not None and user.is_active and user.email:
            recovery.issue_code(
                user, RecoveryCode.Purpose.SELF_RECOVERY, ip=client_ip(request)
            )
        else:
            recovery.logger.info(
                "recovery_requested_no_delivery ip=%s", client_ip(request)
            )
        return Response({"detail": RECOVERY_REQUEST_MESSAGE})


class RecoveryConfirmView(APIView):
    """Step 2: send the code and a new password/PIN. On success the user is signed in."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "recovery_confirm"

    @extend_schema(
        tags=["Auth"],
        summary="Account recovery: confirm code and set new password",
        request=RecoveryConfirmSerializer,
        responses={200: TokenPairSerializer},
    )
    def post(self, request):
        serializer = RecoveryConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        if not recovery.mark_used(serializer.validated_data["recovery_code"]):
            raise ValidationError(
                {"code": ["Invalid or expired code. Request a new one."]}
            )
        recovery.set_new_password(user, serializer.validated_data["new_password"])
        recovery.logger.info(
            "recovery_succeeded user=%s ip=%s", user.pk, client_ip(request)
        )
        return Response(TokenPairSerializer.for_user(user))


# --- Email verification: prove the registered email address is really the user's ---------------


class EmailVerifyResendView(APIView):
    """Send a new verification code to the signed-in user's email."""

    permission_classes = [IsAuthenticated]
    throttle_scope = "email_verify_resend"

    @extend_schema(
        tags=["Auth"],
        summary="Email verification: send me a new code",
        request=None,
        responses={200: DetailSerializer},
    )
    def post(self, request):
        user = request.user
        if not user.email:
            raise ValidationError(
                {"detail": "Your account has no email address to verify."}
            )
        if user.email_verified:
            raise ValidationError({"detail": "Your email is already verified."})
        result = recovery.issue_code(
            user, RecoveryCode.Purpose.EMAIL_VERIFY, ip=client_ip(request)
        )
        if result != "sent":
            raise ValidationError({"detail": EMAIL_CODE_ERRORS[result]})
        return Response(
            {"detail": f"We sent a code to {recovery.mask_email(user.email)}."}
        )


class EmailVerifyConfirmView(APIView):
    """Enter the 6-digit code from the email. On success the email is marked verified."""

    permission_classes = [IsAuthenticated]
    throttle_scope = "email_verify_confirm"

    @extend_schema(
        tags=["Auth"],
        summary="Email verification: confirm the code",
        request=EmailVerifyConfirmSerializer,
        responses={200: UserSerializer},
    )
    def post(self, request):
        serializer = EmailVerifyConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user
        if not user.email:
            raise ValidationError(
                {"detail": "Your account has no email address to verify."}
            )
        if user.email_verified:
            return Response(UserSerializer(user).data)
        rc = recovery.check_code(
            user, serializer.validated_data["code"], RecoveryCode.Purpose.EMAIL_VERIFY
        )
        if rc is None or not recovery.mark_used(rc):
            raise ValidationError(
                {"code": ["Invalid or expired code. Request a new one."]}
            )
        recovery.mark_email_verified(user)
        recovery.logger.info(
            "email_verified user=%s ip=%s", user.pk, client_ip(request)
        )
        return Response(UserSerializer(user).data)


class PasswordChangeView(APIView):
    """Change your own password/PIN. Clears `must_change_password` and signs out other devices."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Auth"],
        summary="Change my password/PIN",
        request=PasswordChangeSerializer,
        responses={200: TokenPairSerializer},
    )
    def post(self, request):
        serializer = PasswordChangeSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        recovery.set_new_password(
            request.user, serializer.validated_data["new_password"]
        )
        return Response(TokenPairSerializer.for_user(request.user))


class AdminResetBase(APIView):
    """Shared checks for admin-assisted resets (users who registered without an email)."""

    permission_classes = [IsAdminRole]

    def get_target(self, request, pk):
        target = get_object_or_404(User, pk=pk)
        if target == request.user:
            self.fail("Use account recovery or change password for your own account.")
        if target.role == User.Role.ADMIN and not request.user.is_superuser:
            raise PermissionDenied(
                "Only a super-admin can reset another administrator."
            )
        if not request.user.email:
            self.fail(
                "Add an email address to your admin account first. The confirmation code is sent there."
            )
        return target

    def fail(self, message):
        raise ValidationError({"detail": message})


class AdminResetRequestView(AdminResetBase):
    """Admin step 1: send a confirmation code to the ADMIN's own email.

    Verify the user's identity offline first (see docs/design/V2-008-account-recovery.md).
    """

    @extend_schema(
        tags=["Manage"],
        summary="Admin reset: email me a confirmation code",
        request=None,
        responses={200: DetailSerializer},
    )
    def post(self, request, pk):
        target = self.get_target(request, pk)
        result = recovery.issue_code(
            request.user,
            RecoveryCode.Purpose.ADMIN_RESET,
            target_user=target,
            ip=client_ip(request),
        )
        if result != "sent":
            self.fail(ADMIN_CODE_ERRORS[result])
        return Response(
            {
                "detail": f"Confirmation code sent to {recovery.mask_email(request.user.email)}."
            }
        )


class AdminResetConfirmView(AdminResetBase):
    """Admin step 2: enter the code from your email and a temporary password/PIN for the user.

    The user must change it at their next sign-in (`must_change_password` = true).
    """

    @extend_schema(
        tags=["Manage"],
        summary="Admin reset: confirm code and set a temporary password",
        request=AdminResetConfirmSerializer,
        responses={200: DetailSerializer},
    )
    def post(self, request, pk):
        target = self.get_target(request, pk)
        serializer = AdminResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        rc = recovery.check_code(
            request.user,
            serializer.validated_data["code"],
            RecoveryCode.Purpose.ADMIN_RESET,
            target_user=target,
        )
        if rc is None:
            raise ValidationError(
                {"code": ["Invalid or expired code. Request a new one."]}
            )
        check_new_password(
            serializer.validated_data["temporary_password"],
            target,
            field="temporary_password",
        )
        if not recovery.mark_used(rc):
            raise ValidationError(
                {"code": ["Invalid or expired code. Request a new one."]}
            )
        recovery.set_new_password(
            target, serializer.validated_data["temporary_password"], must_change=True
        )
        recovery.logger.warning(
            "admin_reset_completed admin=%s user=%s ip=%s",
            request.user.pk,
            target.pk,
            client_ip(request),
        )
        return Response(
            {
                "detail": "Temporary password set. Give it to the user in person; "
                "they must choose a new one at their next sign-in."
            }
        )


class BaseAvailabilityViewSet(viewsets.ModelViewSet):
    serializer_class = BaseAvailabilitySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # Users should only manage their own core schedule
        return BaseAvailability.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class AvailabilityOverrideViewSet(viewsets.ModelViewSet):
    serializer_class = AvailabilityOverrideSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return AvailabilityOverride.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class RoutingAvailabilityViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint for the request router to discover available resources
    within a specific time dimension or boundary.
    """

    serializer_class = AvailabilityPeriodSerializer

    def get_queryset(self):
        queryset = AvailabilityPeriod.objects.filter(is_active=True).select_related(
            "resource"
        )

        start_window = self.request.query_params.get("start_window")
        end_window = self.request.query_params.get("end_window")
        resource_id = self.request.query_params.get("resource_id")

        if resource_id:
            queryset = queryset.filter(resource_id=resource_id)

        if start_window and end_window:
            parsed_start = parse_datetime(start_window)
            parsed_end = parse_datetime(end_window)

            if parsed_start and parsed_end:
                queryset = queryset.filter(
                    start_time__lte=parsed_end, end_time__gte=parsed_start
                )

        return queryset
