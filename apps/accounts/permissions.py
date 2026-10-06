from rest_framework.permissions import BasePermission


class IsActiveAdmin(BasePermission):
    """Signed-in user with role "admin".

    TODO: switch to apps.core.permissions.IsAdmin once Shadia's permissions PR is merged.
    """

    message = "Only administrators can do this."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_active and user.role == "admin")
