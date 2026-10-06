"""Role-based permission classes shared by every app.

Use them on a view to limit it to one or more roles:

    class InterpreterAvailabilityView(APIView):
        permission_classes = [IsInterpreter]

    class SessionListView(ListAPIView):
        permission_classes = [IsDeafUser | IsInstitution]   # either role

Not signed in -> 401. Signed in with another role -> 403.
"""
from django.contrib.auth import get_user_model
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
