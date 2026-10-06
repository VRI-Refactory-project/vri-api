from drf_spectacular.contrib.rest_framework_simplejwt import SimpleJWTScheme
from rest_framework.exceptions import PermissionDenied
from rest_framework_simplejwt.authentication import JWTAuthentication

# Endpoints a user may still call while they must change a temporary password (after an admin reset).
ALLOWED_WHILE_MUST_CHANGE = {"auth-password-change", "auth-me"}


class EnforcePasswordChangeJWTAuthentication(JWTAuthentication):
    """JWT authentication that blocks everything except "change password" (and "me") for users whose
    password was reset by an admin, until they choose their own. Enforced on the server, not just in the app."""

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is not None:
            user, _ = result
            match = getattr(request._request, "resolver_match", None)
            if user.must_change_password and (match is None or match.url_name not in ALLOWED_WHILE_MUST_CHANGE):
                raise PermissionDenied({
                    "detail": "You must change your temporary password before continuing.",
                    "code": "password_change_required",
                })
        return result


class EnforcePasswordChangeJWTScheme(SimpleJWTScheme):
    """Tells Swagger this is ordinary Bearer-token auth, so the Authorize button keeps working."""

    target_class = "apps.accounts.authentication.EnforcePasswordChangeJWTAuthentication"
    name = "jwtAuth"
