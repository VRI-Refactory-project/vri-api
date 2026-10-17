from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AvailabilityPeriodViewSet,
    AvailabilityStatusView,
    CertificationUploadView,
    DayAvailabilityView,
    ExceptionDetailView,
    ExceptionListView,
    InterpreterAvailabilityDetailView,
    InterpreterAvailabilityListCreateView,
    InterpreterProfileDetailView,
    WeeklyScheduleView,
)

app_name = "interpreters"

router = DefaultRouter()
router.register(r'availability', AvailabilityPeriodViewSet, basename='availability')

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
        "me/availability/exceptions/",
        ExceptionListView.as_view(),
        name="availability-exceptions",
    ),
    path(
        "me/availability/exceptions/<str:day>/",
        ExceptionDetailView.as_view(),
        name="availability-exception-detail",
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
    path('', include(router.urls)),
]
