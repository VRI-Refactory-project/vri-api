from django.urls import path
from .views import (
    SessionRequestListCreateView,
    SessionRequestDetailView,
    SessionStatusView,
)

app_name = "service_requests"

urlpatterns = [
    path(
        "",
        SessionRequestListCreateView.as_view(),
        name="session-request-list-create",
    ),
    path(
        "<int:pk>/",
        SessionRequestDetailView.as_view(),
        name="session-request-detail",
    ),
    path(
        "<int:pk>/status/",
        SessionStatusView.as_view(),
        name="session-request-status",
    ),
]
