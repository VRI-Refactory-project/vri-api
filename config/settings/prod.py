from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403
from .base import EMAIL_BACKEND, SECRET_KEY

# Refuse to start with settings that would leak recovery codes or make login tokens forgeable.
if EMAIL_BACKEND.endswith("console.EmailBackend"):
    raise ImproperlyConfigured("Set EMAIL_URL in production: recovery codes would be printed to the logs.")
if len(SECRET_KEY) < 50 or SECRET_KEY.startswith(("change-me", "django-insecure")):
    raise ImproperlyConfigured("Set a long random SECRET_KEY (50+ characters) in production.")

DEBUG = False
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Docs are only public in dev; in prod only staff can open them.
SPECTACULAR_SETTINGS = {
    **SPECTACULAR_SETTINGS,  # noqa: F405
    "SERVE_PERMISSIONS": ["rest_framework.permissions.IsAdminUser"],
}
