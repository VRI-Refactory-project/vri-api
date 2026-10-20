import logging

from django.conf import settings
from django.contrib import admin
from django.db import connection
from django.http import JsonResponse
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

from apps.service_requests.services import sweep
from apps.service_requests.views import InterpreterOffersView

logger = logging.getLogger(__name__)


def health(request):
    """Answer "ok" after a trivial database query.

    An uptime monitor pinging this keeps the free Render server and the free Neon database awake.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    if settings.DISPATCH_REQUESTS_ON_HEALTH:
        # The monitor calls this every few minutes, which is how the request queue moves on the free
        # server. A problem here must never make the health check itself fail.
        try:
            sweep()
        except Exception:
            logger.exception("Request queue sweep failed")
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("api/health/", health, name="health"),
    # Django Admin
    path("admin/", admin.site.urls),
    # API v1 Endpoints
    path("api/v1/", include("apps.accounts.urls")),
    path(
        "api/v1/interpreters/me/requests/",
        InterpreterOffersView.as_view(),
        name="interpreter-offers",
    ),  # V2-022, requests offered to the signed-in interpreter
    path("api/v1/interpreters/", include("apps.interpreters.urls")),
    path(
        "api/v1/manage/interpreters/",
        include("apps.interpreters.manage_urls"),
    ),  # V2-045, admins only
    path("api/v1/dashboard/", include("apps.dashboards.urls")),
    path(
        "api/v1/institutions/",
        include("apps.institutions.urls"),
    ),  # V2-005, the institution's own data
    path("api/v1/auth/", include("apps.users.urls")),  # Web Registration endpoint
    path(
        "api/v1/administration/",
        include("apps.administration.urls"),
    ),
    # OpenAPI Schema & Documentation
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path(
        "api/redoc/",
        SpectacularRedocView.as_view(url_name="schema"),
        name="redoc",
    ),
    path(
        "api/v1/notifications/",
        include("apps.notifications.urls"),
    ),
    path(
        "api/v1/requests/",
        include("apps.service_requests.urls"),
    ),
]
