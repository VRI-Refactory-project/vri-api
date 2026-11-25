# """The administrator's user list: everyone on the platform, searchable and filterable."""

# from datetime import timedelta

# from django.contrib.auth import get_user_model
# from django.db.models import CharField, Case, Q, Value, When
# from django.utils import timezone
# from drf_spectacular.types import OpenApiTypes
# from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
# from rest_framework import serializers
# from rest_framework.exceptions import ValidationError
# from rest_framework.response import Response
# from rest_framework.views import APIView

# from apps.core.permissions import IsAdminRole
# from apps.institutions.models import InstitutionStatus
# from apps.interpreters.models import VerificationStatus

# User = get_user_model()

# MAX_USERS = 500

# # The web page says "deaf"; the API's role value is "deaf_hoh".
# ROLE_FILTERS = {
#     "deaf": User.Role.DEAF_HOH,
#     "deaf_hoh": User.Role.DEAF_HOH,
#     "interpreter": User.Role.INTERPRETER,
#     "institution": User.Role.INSTITUTION,
#     "admin": User.Role.ADMIN,
# }
# STATUS_FILTERS = ("verified", "pending", "rejected", "suspended")
# RANGE_FILTERS = {"7-days": 7, "30-days": 30}


# def with_verification_status(queryset):
#     """Add `verification_status` to every user: verified, pending, rejected or suspended.

#     - A deactivated account is suspended, whatever its role.
#     - Interpreters follow their profile: approved, waiting for review, rejected, or switched off.
#     - Institution users follow their institution (and are suspended once their access is revoked).
#     - Deaf users and administrators have nothing to verify, so an active account counts as verified.
#     """
#     interpreter = User.Role.INTERPRETER
#     institution = User.Role.INSTITUTION
#     return queryset.annotate(
#         verification_status=Case(
#             When(is_active=False, then=Value("suspended")),
#             When(
#                 role=interpreter,
#                 interpreter_profile__verification_status=VerificationStatus.APPROVED,
#                 then=Value("verified"),
#             ),
#             When(
#                 role=interpreter,
#                 interpreter_profile__verification_status=VerificationStatus.REJECTED,
#                 then=Value("rejected"),
#             ),
#             When(
#                 role=interpreter,
#                 interpreter_profile__verification_status=VerificationStatus.DEACTIVATED,
#                 then=Value("suspended"),
#             ),
#             When(role=interpreter, then=Value("pending")),
#             When(
#                 role=institution,
#                 institution_staff__revoked_at__isnull=False,
#                 then=Value("suspended"),
#             ),
#             When(
#                 role=institution,
#                 institution_staff__institution__status=InstitutionStatus.APPROVED,
#                 then=Value("verified"),
#             ),
#             When(
#                 role=institution,
#                 institution_staff__institution__status=InstitutionStatus.REJECTED,
#                 then=Value("rejected"),
#             ),
#             When(
#                 role=institution,
#                 institution_staff__institution__status=InstitutionStatus.DELETED,
#                 then=Value("suspended"),
#             ),
#             When(role=institution, then=Value("pending")),
#             default=Value("verified"),
#             output_field=CharField(),
#         )
#     )


# def search_filter(text):
#     """Match name, email or phone number. A local number (0772...) also matches +256772..."""
#     match = (
#         Q(full_name__icontains=text)
#         | Q(email__icontains=text)
#         | Q(phone_number__icontains=text)
#     )
#     digits = text.replace(" ", "")
#     if digits.isdigit() and digits.startswith("0") and len(digits) > 1:
#         match |= Q(phone_number__icontains=digits.lstrip("0"))
#     return match


# class AdminUserListView(APIView):
#     """Everyone on the platform, newest first (at most 500), with a simple verification status."""

#     permission_classes = [IsAdminRole]

#     @extend_schema(
#         tags=["Administration Users"],
#         summary="List users (administrators only)",
#         parameters=[
#             OpenApiParameter(
#                 "search", OpenApiTypes.STR, description="Name, email or phone number."
#             ),
#             OpenApiParameter(
#                 "role",
#                 OpenApiTypes.STR,
#                 enum=sorted(ROLE_FILTERS),
#                 description="Only this type of user.",
#             ),
#             OpenApiParameter(
#                 "status",
#                 OpenApiTypes.STR,
#                 enum=list(STATUS_FILTERS),
#                 description="Only this verification status.",
#             ),
#             OpenApiParameter(
#                 "range",
#                 OpenApiTypes.STR,
#                 enum=sorted(RANGE_FILTERS),
#                 description="Only users who registered in the last 7 or 30 days.",
#             ),
#         ],
#         responses={
#             200: inline_serializer(
#                 "AdminUser",
#                 {
#                     "id": serializers.IntegerField(),
#                     "full_name": serializers.CharField(),
#                     "email": serializers.CharField(),
#                     "phone_number": serializers.CharField(),
#                     "role": serializers.CharField(),
#                     "verification_status": serializers.CharField(),
#                     "created_at": serializers.DateTimeField(),
#                     "last_login": serializers.DateTimeField(allow_null=True),
#                 },
#                 many=True,
#             )
#         },
#     )
#     def get(self, request):
#         params = request.query_params
#         users = with_verification_status(User.objects.all())

#         search = params.get("search", "").strip()
#         if search:
#             users = users.filter(search_filter(search))

#         role = params.get("role", "")
#         if role:
#             if role not in ROLE_FILTERS:
#                 raise ValidationError({"role": [f"Choose one of: {', '.join(sorted(ROLE_FILTERS))}."]})
#             users = users.filter(role=ROLE_FILTERS[role])

#         status_filter = params.get("status", "")
#         if status_filter:
#             if status_filter not in STATUS_FILTERS:
#                 raise ValidationError({"status": [f"Choose one of: {', '.join(STATUS_FILTERS)}."]})
#             users = users.filter(verification_status=status_filter)

#         range_filter = params.get("range", "")
#         if range_filter:
#             if range_filter not in RANGE_FILTERS:
#                 raise ValidationError({"range": [f"Choose one of: {', '.join(sorted(RANGE_FILTERS))}."]})
#             since = timezone.now() - timedelta(days=RANGE_FILTERS[range_filter])
#             users = users.filter(date_joined__gte=since)

#         return Response(
#             [
#                 {
#                     "id": user.pk,
#                     "full_name": user.full_name,
#                     "email": user.email or "",
#                     "phone_number": str(user.phone_number),
#                     "role": user.role,
#                     "verification_status": user.verification_status,
#                     "created_at": user.date_joined,
#                     "last_login": user.last_login,
#                 }
#                 for user in users.order_by("-date_joined", "-pk")[:MAX_USERS]
#             ]
#         )


"""The administrator's user list: everyone on the platform, searchable and filterable."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import CharField, Case, Q, Value, When
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit import services as audit
from apps.audit.models import AuditLog
from apps.core.permissions import IsAdminRole
from apps.institutions.models import InstitutionStatus
from apps.interpreters.models import VerificationStatus

User = get_user_model()

# The web page says "deaf"; the API's role value is "deaf_hoh".
ROLE_FILTERS = {
    "deaf": User.Role.DEAF_HOH,
    "deaf_hoh": User.Role.DEAF_HOH,
    "interpreter": User.Role.INTERPRETER,
    "institution": User.Role.INSTITUTION,
    "admin": User.Role.ADMIN,
}
STATUS_FILTERS = ("verified", "pending", "rejected", "suspended")
RANGE_FILTERS = {"7-days": 7, "30-days": 30}


def with_verification_status(queryset):
    """Add `verification_status` to every user: verified, pending, rejected or suspended.

    - A deactivated account is suspended, whatever its role.
    - Interpreters follow their profile: approved, waiting for review, rejected, or switched off.
    - Institution users follow their institution (and are suspended once their access is revoked).
    - Deaf users and administrators have nothing to verify, so an active account counts as verified.
    """
    interpreter = User.Role.INTERPRETER
    institution = User.Role.INSTITUTION
    return queryset.annotate(
        verification_status=Case(
            When(is_active=False, then=Value("suspended")),
            When(
                role=interpreter,
                interpreter_profile__verification_status=VerificationStatus.APPROVED,
                then=Value("verified"),
            ),
            When(
                role=interpreter,
                interpreter_profile__verification_status=VerificationStatus.REJECTED,
                then=Value("rejected"),
            ),
            When(
                role=interpreter,
                interpreter_profile__verification_status=VerificationStatus.DEACTIVATED,
                then=Value("suspended"),
            ),
            When(role=interpreter, then=Value("pending")),
            When(
                role=institution,
                institution_staff__revoked_at__isnull=False,
                then=Value("suspended"),
            ),
            When(
                role=institution,
                institution_staff__institution__status=InstitutionStatus.APPROVED,
                then=Value("verified"),
            ),
            When(
                role=institution,
                institution_staff__institution__status=InstitutionStatus.REJECTED,
                then=Value("rejected"),
            ),
            When(
                role=institution,
                institution_staff__institution__status=InstitutionStatus.DELETED,
                then=Value("suspended"),
            ),
            When(role=institution, then=Value("pending")),
            default=Value("verified"),
            output_field=CharField(),
        )
    )


def search_filter(text):
    """Match name, email or phone number. A local number (0772...) also matches +256772..."""
    match = Q(full_name__icontains=text) | Q(email__icontains=text) | Q(phone_number__icontains=text)
    digits = text.replace(" ", "")
    if digits.isdigit() and digits.startswith("0") and len(digits) > 1:
        match |= Q(phone_number__icontains=digits.lstrip("0"))
    return match


class AdminUserListView(APIView):
    """Everyone on the platform, newest first (at most 500), with a simple verification status."""

    permission_classes = [IsAdminRole]

    @extend_schema(
        tags=["Administration Users"],
        summary="List users (administrators only)",
        parameters=[
            OpenApiParameter("search", OpenApiTypes.STR, description="Name, email or phone number."),
            OpenApiParameter("page", OpenApiTypes.INT, description="Page number."),
            OpenApiParameter("page_size", OpenApiTypes.INT, description="Results per page (maximum 100)."),
            OpenApiParameter(
                "role",
                OpenApiTypes.STR,
                enum=sorted(ROLE_FILTERS),
                description="Only this type of user.",
            ),
            OpenApiParameter(
                "status",
                OpenApiTypes.STR,
                enum=list(STATUS_FILTERS),
                description="Only this verification status.",
            ),
            OpenApiParameter(
                "range",
                OpenApiTypes.STR,
                enum=sorted(RANGE_FILTERS),
                description="Only users who registered in the last 7 or 30 days.",
            ),
        ],
        responses={
            200: inline_serializer(
                "AdminUser",
                {
                    "id": serializers.IntegerField(),
                    "full_name": serializers.CharField(),
                    "email": serializers.CharField(),
                    "phone_number": serializers.CharField(),
                    "role": serializers.CharField(),
                    "verification_status": serializers.CharField(),
                    "created_at": serializers.DateTimeField(),
                    "last_login": serializers.DateTimeField(allow_null=True),
                },
                many=True,
            )
        },
    )
    def get(self, request):
        params = request.query_params
        users = with_verification_status(User.objects.all())

        search = params.get("search", "").strip()
        if search:
            users = users.filter(search_filter(search))

        role = params.get("role", "")
        if role:
            if role not in ROLE_FILTERS:
                raise ValidationError({"role": [f"Choose one of: {', '.join(sorted(ROLE_FILTERS))}."]})
            users = users.filter(role=ROLE_FILTERS[role])

        status_filter = params.get("status", "")
        if status_filter:
            if status_filter not in STATUS_FILTERS:
                raise ValidationError({"status": [f"Choose one of: {', '.join(STATUS_FILTERS)}."]})
            users = users.filter(verification_status=status_filter)

        range_filter = params.get("range", "")
        if range_filter:
            if range_filter not in RANGE_FILTERS:
                raise ValidationError({"range": [f"Choose one of: {', '.join(sorted(RANGE_FILTERS))}."]})
            since = timezone.now() - timedelta(days=RANGE_FILTERS[range_filter])
            users = users.filter(date_joined__gte=since)

        users = users.order_by("-date_joined", "-pk")
        paginator = PageNumberPagination()
        paginator.page_size = 10
        paginator.page_size_query_param = "page_size"
        paginator.max_page_size = 100
        page = paginator.paginate_queryset(users, request, view=self)
        return Response(
            {
                "count": paginator.page.paginator.count,
                "results": [
                    {
                        "id": user.pk,
                        "full_name": user.full_name,
                        "email": user.email or "",
                        "phone_number": str(user.phone_number),
                        "role": user.role,
                        "verification_status": user.verification_status,
                        "created_at": user.date_joined,
                        "last_login": user.last_login,
                    }
                    for user in page
                ],
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
            }
        )


# --- NEW ACTION VIEWS ---


class AdminUserApproveView(APIView):
    """Approve a user (currently only interpreters and institutions have an approval state)."""

    permission_classes = [IsAdminRole]

    def post(self, request, pk):
        user = get_object_or_404(User, pk=pk)

        if user.role == User.Role.INTERPRETER and hasattr(user, "interpreter_profile"):
            profile = user.interpreter_profile
            profile.verification_status = VerificationStatus.APPROVED
            profile.save(update_fields=["verification_status"])

            audit.record(
                AuditLog.Event.INTERPRETER_APPROVED,
                request.user,
                request,
                target_user=user,
            )
            return Response({"detail": f"{user.full_name} approved as interpreter."})

        if user.role == User.Role.INSTITUTION and hasattr(user, "institution_profile"):
            institution = user.institution_profile
            institution.status = InstitutionStatus.APPROVED
            institution.save(update_fields=["status"])

            audit.record(
                AuditLog.Event.INSTITUTION_APPROVED,
                request.user,
                request,
                target_user=user,
            )
            return Response({"detail": f"{user.full_name} approved as institution."})

        return Response(
            {"detail": "Only interpreters and institutions can be approved."},
            status=status.HTTP_400_BAD_REQUEST,
        )


class AdminUserDeactivateView(APIView):
    """Deactivate a user. Their status becomes 'suspended' everywhere."""

    permission_classes = [IsAdminRole]

    def post(self, request, pk):
        user = get_object_or_404(User, pk=pk)

        if user.is_active:
            user.is_active = False
            user.save(update_fields=["is_active"])

            audit.record(
                AuditLog.Event.ADMIN_DEACTIVATED_USER,
                request.user,
                request,
                target_user=user,
            )
            return Response({"detail": f"{user.full_name} deactivated."})

        return Response({"detail": "User is already inactive."})


class AdminUserDeleteView(APIView):
    """Permanently delete a user and all their related records (cascades)."""

    permission_classes = [IsAdminRole]

    def delete(self, request, pk):
        user = get_object_or_404(User, pk=pk)

        if user.pk == request.user.pk:
            return Response(
                {"detail": "You cannot delete your own admin account."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            name = user.full_name
            audit.record(
                AuditLog.Event.ADMIN_DELETED_USER,
                request.user,
                request,
                target_user=user,
                details={"email": user.email},
            )
            user.delete()

        return Response({"detail": f"{name} deleted."}, status=status.HTTP_204_NO_CONTENT)


class AdminUserDetailView(APIView):
    """Retrieve or update a single user."""

    permission_classes = [IsAdminRole]

    def get(self, request, pk):
        user = get_object_or_404(User, pk=pk)
        return Response(self.representation(user))

    @staticmethod
    def representation(user):
        return {
            "id": user.pk, "full_name": user.full_name, "email": user.email or "",
            "phone_number": str(user.phone_number), "role": user.role,
            "verification_status": "verified", "created_at": user.date_joined,
            "last_login": user.last_login,
        }

    def patch(self, request, pk):
        user = get_object_or_404(User, pk=pk)
        original_email = user.email

        # Only allow updating these fields
        name = request.data.get("full_name", request.data.get("name"))
        email = request.data.get("email")
        phone = request.data.get("phone_number", request.data.get("phone"))

        errors = {}
        if name is not None and (not isinstance(name, str) or not name.strip() or len(name.strip()) > 150):
            errors["full_name"] = ["Enter a valid full name (up to 150 characters)."]
        if email is not None:
            from django.core.validators import validate_email
            if not isinstance(email, str):
                errors["email"] = ["Enter a valid email address."]
            elif email.strip():
                try:
                    validate_email(email.strip())
                except Exception:
                    errors["email"] = ["Enter a valid email address."]
            duplicate_email = (
                isinstance(email, str)
                and email.strip()
                and User.objects.filter(email__iexact=email.strip()).exclude(pk=user.pk).exists()
            )
            if duplicate_email:
                errors["email"] = ["An account with this email already exists."]
        if phone is not None:
            from apps.accounts.serializers import PlusPhoneNumberField
            field = PlusPhoneNumberField()
            try:
                phone = field.run_validation(phone)
            except serializers.ValidationError as exc:
                errors["phone_number"] = exc.detail
            else:
                if User.objects.filter(phone_number=phone).exclude(pk=user.pk).exists():
                    errors["phone_number"] = ["An account with this phone number already exists."]
        if errors:
            raise ValidationError(errors)

        fields = []
        for key, value in (("full_name", name), ("email", email), ("phone_number", phone)):
            if value is not None:
                setattr(user, key, value.strip() if isinstance(value, str) else value)
                fields.append(key)
        if "email" in fields and user.email != original_email:
            user.email_verified_at = None
            fields.append("email_verified_at")
        if fields:
            user.save(update_fields=fields)
        return Response(self.representation(user))
