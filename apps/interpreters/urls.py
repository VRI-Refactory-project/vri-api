from django.urls import path

from .views import (
    AvailabilityStatusView,
    CertificationUploadView,
    DayAvailabilityView,
    InterpreterAvailabilityDetailView,
    InterpreterAvailabilityListCreateView,
    InterpreterProfileDetailView,
    WeeklyScheduleView,
)

app_name = "interpreters"

urlpatterns = [
    path(
        "me/",
        InterpreterProfileDetailView.as_view(),
        name="interpreter-profile-detail",
    ),
    path(
        "me/availability/",
        WeeklyScheduleView.as_view(),
        name="availability-week",
    ),
    path(
        "me/availability/status/",
        AvailabilityStatusView.as_view(),
        name="availability-status",
    ),
    path(
        "me/availability/days/<str:day>/",
        DayAvailabilityView.as_view(),
        name="availability-day",
    ),
    path(
        "certifications/upload/",
        CertificationUploadView.as_view(),
        name="certification-upload",
    ),
    path(
        "availability/",
        InterpreterAvailabilityListCreateView.as_view(),
        name="availability-list-create",
    ),
    path(
        "availability/<int:pk>/",
        InterpreterAvailabilityDetailView.as_view(),
        name="availability-detail",
    ),
]
