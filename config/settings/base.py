"""Base settings shared by every environment. Secrets come from .env."""

from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    CORS_ALLOW_ALL_ORIGINS=(bool, False),
)
# Loads .env when running outside Docker. In Docker, compose injects the same values.
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "rest_framework",
    "drf_spectacular",
    "drf_spectacular_sidecar",  # Swagger UI / ReDoc files served by us, not a CDN
    "corsheaders",
    "phonenumber_field",
    "rest_framework_simplejwt.token_blacklist",
    # Local apps
    "apps.accounts",
    "apps.interpreters",
    "apps.institutions",
    "apps.users",
    "apps.audit",
    "apps.administration",
    "apps.notifications",
    "apps.service_requests",

]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",  # must be first
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.accounts.middleware.PreventSharedBrowserJWTMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# PostgreSQL. Hosted databases (Neon, Render, ...) give one DATABASE_URL, which wins when set.
# Otherwise the POSTGRES_* values configure the db container in docker-compose.yml.
if env("DATABASE_URL", default=""):
    DATABASES = {"default": env.db("DATABASE_URL")}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("POSTGRES_DB"),
            "USER": env("POSTGRES_USER"),
            "PASSWORD": env("POSTGRES_PASSWORD"),
            "HOST": env(
                "DATABASE_HOST", default="db"
            ),  # "db" = service name in docker-compose.yml
            "PORT": env("DATABASE_PORT", default="5432"),
        }
    }

# --- Users and authentication ---
AUTH_USER_MODEL = "accounts.User"

# Users sign in with phone number OR email (see apps/accounts/backends.py).
AUTHENTICATION_BACKENDS = ["apps.accounts.backends.PhoneOrEmailBackend"]

# Local numbers like 0772123456 are read as Ugandan and stored as +256772123456.
PHONENUMBER_DEFAULT_REGION = "UG"
PHONENUMBER_DB_FORMAT = "E164"

# Argon2 first; existing PBKDF2 hashes still verify and are upgraded on next login.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

# Password or numeric PIN, minimum 6 characters. Common PINs/passwords (123456, password...)
# are rejected, and login attempts are rate-limited (see "login" throttle below).
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "apps.core.validators.NumberAndSpecialCharacterValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Kampala"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Uploaded files (e.g. interpreter certificates) are saved here (git-ignored). There is deliberately no public
# MEDIA_URL route: certificates are personal documents and admins download them through a protected endpoint.
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Cache (rate limits). Local memory is fine for development; production needs a shared cache,
# e.g. CACHE_URL=redis://redis:6379/1 (needs the "redis" package) or dbcache://django_cache.
CACHES = {"default": env.cache_url("CACHE_URL", default="locmemcache://")}

# --- Email (account recovery codes) ---
# Default prints emails in the backend logs, so codes are visible during development:
#   docker compose logs -f backend
# Production: set EMAIL_URL, e.g. smtp+tls://user:password@smtp.example.com:587
EMAIL_CONFIG = env.email_url("EMAIL_URL", default="consolemail://")
vars().update(EMAIL_CONFIG)
DEFAULT_FROM_EMAIL = env(
    "DEFAULT_FROM_EMAIL", default="SignConnect <no-reply@signconnect.local>"
)
BREVO_API_KEY = env("BREVO_API_KEY", default="")
# Public address of the white SignConnect logo shown at the top of emails (served by the web app,
# e.g. https://<web-address>/email-logo.png). Empty = the header shows the name as text.
EMAIL_LOGO_URL = env("EMAIL_LOGO_URL", default="")

# --- Expo Push Notifications ---
EXPO_PUSH_ENABLED = env.bool("EXPO_PUSH_ENABLED", default=False)
EXPO_ACCESS_TOKEN = env("EXPO_ACCESS_TOKEN", default="")

# --- Interpreter requests (V2-022) ---
# How long an interpreter has to answer an offer, and how long a request may wait before it fails.
REQUEST_OFFER_EXPIRY_SECONDS = env.int("REQUEST_OFFER_EXPIRY_SECONDS", default=60)
REQUEST_MAX_WAIT_SECONDS = env.int("REQUEST_MAX_WAIT_SECONDS", default=300)
# Lead time (in seconds) before an offer expires to send an urgency reminder push notification (V2-037-BE-03)
INTERPRETER_OFFER_REMINDER_LEAD_TIME = env.int(
    "INTERPRETER_OFFER_REMINDER_LEAD_TIME", default=15
)
# Move the request queue forward every time the uptime monitor calls /api/health/. Switch it on when
# no scheduler is available (Render free). Off by default.
DISPATCH_REQUESTS_ON_HEALTH = env.bool("DISPATCH_REQUESTS_ON_HEALTH", default=False)
# A waiting request is cancelled when the requester's app has not checked on it for this long (V2-009).
# Placeholder until the product owner decides (REQUESTER_DISCONNECT_GRACE_WINDOW is TBD).
REQUESTER_DISCONNECT_GRACE_WINDOW = timedelta(
    seconds=env.int("REQUESTER_DISCONNECT_GRACE_WINDOW_SECONDS", default=120)
)

# --- Logging (recovery events are logged for audit; codes and passwords never are) ---
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"apps.accounts.recovery": {"handlers": ["console"], "level": "INFO"}},
}

# --- CORS (React web dashboard + React Native app) ---
CORS_ALLOW_ALL_ORIGINS = env("CORS_ALLOW_ALL_ORIGINS")
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])

REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.accounts.authentication.EnforcePasswordChangeJWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.ScopedRateThrottle",
    ],
    # Number of proxies in front of Django (nginx, load balancer). 0 locally. Without this, every user
    # looks like the proxy's IP and rate limits apply to the whole platform at once.
    "NUM_PROXIES": env.int("NUM_PROXIES", default=0),
    "DEFAULT_THROTTLE_RATES": {
        "login": "10/minute",
        "register": "20/hour",
        "availability_check": "60/minute",
        "recovery_request": "5/hour",
        "recovery_confirm": "10/minute",
        "email_verify_resend": "5/hour",
        "email_verify_confirm": "10/minute",
    },
}

# --- JWT (mobile app and web dashboards send: Authorization: Bearer <access token>) ---
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "AUTH_HEADER_TYPES": ("Bearer",),
    "UPDATE_LAST_LOGIN": True,
}

# --- Swagger / OpenAPI (drf-spectacular) ---
SPECTACULAR_SETTINGS = {
    "TITLE": "SignConnect VRI API",
    "DESCRIPTION": "Backend API for the SignConnect Video Remote Interpreting platform.",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": r"/api/v[0-9]+",
    "COMPONENT_SPLIT_REQUEST": True,
    "SWAGGER_UI_SETTINGS": {"persistAuthorization": True},
    # One name for a choice list that several fields share (otherwise Swagger warns about it).
    "ENUM_NAME_OVERRIDES": {
        "VerificationStatusEnum": "apps.interpreters.models.VerificationStatus",
        "StaffRoleEnum": "apps.institutions.models.StaffRole",  # V2-006, so it is not confused with user roles
        "ServiceRequestStatusEnum": "apps.service_requests.models.ServiceRequest.Status",  # V2-009
        "SpecialisationEnum": "apps.interpreters.models.Specialisation",
    },
    # Serve the docs UI from our own static files. Some networks (e.g. campus Wi-Fi) block
    # cdn.jsdelivr.net, which leaves /api/docs/ as a blank page.
    "SWAGGER_UI_DIST": "SIDECAR",
    "SWAGGER_UI_FAVICON_HREF": "SIDECAR",
    "REDOC_DIST": "SIDECAR",
}
