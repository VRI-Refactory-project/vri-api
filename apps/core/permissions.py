"""Role-based permission classes shared by every app.

Use them on a view to limit it to one or more roles:

    class InterpreterAvailabilityView(APIView):
        permission_classes = [IsInterpreter]

    class SessionListView(ListAPIView):
        permission_classes = [IsDeafUser | IsInstitution]   # either role

Not signed in -> 401. Signed in with another role -> 403.
"""

from django.contrib.auth import get_user_model
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

User = get_user_model()


class HasRole(BasePermission):
    """Allows signed-in users whose role is in `allowed_roles`.

    The role is read from the database (request.user), not from the token, so a role change
    or deactivation takes effect on the next request.
    """

    allowed_roles = ()
    message = "Your account type does not have access to this resource."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role in self.allowed_roles)


class IsDeafUser(HasRole):
    allowed_roles = (User.Role.DEAF_HOH,)


class IsInterpreter(HasRole):
    allowed_roles = (User.Role.INTERPRETER,)


class IsInstitution(HasRole):
    allowed_roles = (User.Role.INSTITUTION,)


class HasInstitutionalRole(HasRole):
    """Allows authenticated users with the institutional role."""

    allowed_roles = (User.Role.INSTITUTION,)


class IsAdminRole(HasRole):
    allowed_roles = (User.Role.ADMIN,)


class IsSignedOut(BasePermission):
    """For creating an account: refuses anyone already signed in (403, code already_signed_in).

    Otherwise a signed-in person could make a second account and be switched to it, leaving the first
    session running. Works on views without authentication classes too: the Bearer token is checked
    here. A missing, expired or invalid token counts as signed out, so an old token left in a browser
    never blocks a real sign-up.
    """

    def has_permission(self, request, view):
        signed_in = request.user and request.user.is_authenticated
        if not signed_in:
            from rest_framework_simplejwt.authentication import JWTAuthentication
            from rest_framework_simplejwt.exceptions import AuthenticationFailed, InvalidToken

            try:
                signed_in = JWTAuthentication().authenticate(request) is not None
            except (InvalidToken, AuthenticationFailed):
                signed_in = False
        if signed_in:
            raise PermissionDenied(
                {
                    "detail": "You're already signed in. Sign out before creating another account.",
                    "code": "already_signed_in",
                }
            )
        return True


def user_staff_membership(user):
    """The signed-in user's active membership of an institution (V2-006), or None.

    Every institution user has one: the person who registered the institution is its first ADMIN.
    """
    if not (user and user.is_authenticated and user.role == User.Role.INSTITUTION):
        return None
    from apps.institutions.models import InstitutionStaff  # avoids an app-loading cycle

    return (
        InstitutionStaff.objects.select_related("institution")
        .filter(user=user, revoked_at__isnull=True)
        .first()
    )


def user_institution(user):
    """The institution the signed-in user belongs to, or None. Institution endpoints must only ever read
    and change data through this, never by an id from the request, so each institution stays isolated."""
    membership = user_staff_membership(user)
    return membership.institution if membership else None


class IsApprovedInstitution(BasePermission):
    """Institution users whose institution has been approved (V2-005).

    Pending and rejected institutions can still sign in (to see their status) but get a 403 with a
    `code` the app can show: institution_pending or institution_rejected.
    """

    message = "Only approved institutions have access to this resource."

    def has_permission(self, request, view):
        institution = user_institution(request.user)
        if institution is None:
            return False
        from apps.institutions.models import InstitutionStatus

        if institution.status == InstitutionStatus.PENDING:
            raise PermissionDenied(
                {
                    "detail": "Your institution is waiting for approval by the SignConnect team.",
                    "code": "institution_pending",
                }
            )
        if institution.status == InstitutionStatus.REJECTED:
            raise PermissionDenied(
                {
                    "detail": "Your institution's application was not approved. Please contact the SignConnect team.",
                    "code": "institution_rejected",
                }
            )
        return institution.status == InstitutionStatus.APPROVED


class IsInstitutionAdmin(IsApprovedInstitution):
    """Administrators of an approved institution: they manage its staff (V2-006, SRS FR-INS-006).

    Institutional users (STAFF) get a 403 with code institution_admin_required.
    """

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        from apps.institutions.models import StaffRole

        if user_staff_membership(request.user).role != StaffRole.ADMIN:
            raise PermissionDenied(
                {
                    "detail": "Only your institution's administrators can manage staff accounts.",
                    "code": "institution_admin_required",
                }
            )
        return True


# The permission class for each role, for code that picks the check at runtime.
ROLE_PERMISSIONS = {
    User.Role.DEAF_HOH: IsDeafUser,
    User.Role.INTERPRETER: IsInterpreter,
    User.Role.INSTITUTION: IsInstitution,
    User.Role.ADMIN: IsAdminRole,
}


class CanAccessInterpreterDashboard(BasePermission):
    """Allow only active, verified interpreters to access their dashboard."""

    message = "Only active, approved interpreters can access this dashboard."

    def has_permission(self, request, view):
        user = request.user

        if not (
            user
            and user.is_authenticated
            and user.is_active
            and user.role == User.Role.INTERPRETER
        ):
            return False

        from apps.interpreters.models import InterpreterProfile, VerificationStatus

        return InterpreterProfile.objects.filter(
            user=user,
            verification_status=VerificationStatus.APPROVED,
        ).exists()
