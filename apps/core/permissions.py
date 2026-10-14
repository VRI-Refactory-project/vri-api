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


class IsAdminRole(HasRole):
    allowed_roles = (User.Role.ADMIN,)


def user_institution(user):
    """The institution the signed-in user belongs to, or None. Institution endpoints must only ever read
    and change data through this, never by an id from the request, so each institution stays isolated."""
    if not (user and user.is_authenticated and user.role == User.Role.INSTITUTION):
        return None
    from apps.institutions.models import InstitutionProfile  # avoids an app-loading cycle

    return InstitutionProfile.objects.filter(user=user).first()


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
            raise PermissionDenied({
                "detail": "Your institution is waiting for approval by the SignConnect team.",
                "code": "institution_pending",
            })
        if institution.status == InstitutionStatus.REJECTED:
            raise PermissionDenied({
                "detail": "Your institution's application was not approved. Please contact the SignConnect team.",
                "code": "institution_rejected",
            })
        return institution.status == InstitutionStatus.APPROVED


# The permission class for each role, for code that picks the check at runtime.
ROLE_PERMISSIONS = {
    User.Role.DEAF_HOH: IsDeafUser,
    User.Role.INTERPRETER: IsInterpreter,
    User.Role.INSTITUTION: IsInstitution,
    User.Role.ADMIN: IsAdminRole,
}
