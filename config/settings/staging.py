"""Settings for the public demo / staging deployment (Render + Neon + Vercel).

Unlike prod.py this allows console email (recovery codes appear in the host's logs) and keeps
the Swagger docs public, because reviewers use them. Use prod.py for the real pilot.
"""

from django.core.exceptions import ImproperlyConfigured

from .base import *
from .base import ALLOWED_HOSTS, MIDDLEWARE, SECRET_KEY, env

if len(SECRET_KEY) < 32 or SECRET_KEY.startswith(("change-me", "django-insecure")):
    raise ImproperlyConfigured(
        "Set a long random SECRET_KEY (32+ characters) for staging."
    )

DEBUG = False

# Render tells the app its own hostname; allow it without hand-editing ALLOWED_HOSTS.
_render_host = env("RENDER_EXTERNAL_HOSTNAME", default="")
if _render_host:
    ALLOWED_HOSTS = [*ALLOWED_HOSTS, _render_host]

# The host terminates HTTPS and forwards plain HTTP; trust its header so Django knows the
# request was secure (otherwise SECURE_SSL_REDIRECT would loop forever).
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# Serve static files (admin, Swagger UI) from the app itself; there is no nginx in front.
MIDDLEWARE = [
    MIDDLEWARE[0],
    MIDDLEWARE[1],
    "whitenoise.middleware.WhiteNoiseMiddleware",
    *MIDDLEWARE[2:],
]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}
