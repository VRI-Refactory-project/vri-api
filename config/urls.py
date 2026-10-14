from django.contrib import admin
from django.db import connection
from django.http import JsonResponse
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)


def health(request):
    """Answer "ok" after a trivial database query.

    An uptime monitor pinging this keeps the free Render server and the free Neon database awake.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("api/health/", health, name="health"),
    # Django Admin
    path("admin/", admin.site.urls),
    # API v1 Endpoints
    path("api/v1/", include("apps.accounts.urls")),
    path("api/v1/interpreters/", include("apps.interpreters.urls")),
    path("api/v1/manage/interpreters/", include("apps.interpreters.manage_urls")),  # V2-045, admins only
    path("api/v1/dashboard/", include("apps.dashboards.urls")),
    path("api/v1/institutions/", include("apps.institutions.urls")),  # V2-005, the institution's own data
    path("api/v1/manage/institutions/", include("apps.institutions.manage_urls")),  # V2-005, admins only
    path("api/v1/auth/", include("apps.users.urls")),  # Web Registration endpoint
    # OpenAPI Schema & Documentation
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
]
