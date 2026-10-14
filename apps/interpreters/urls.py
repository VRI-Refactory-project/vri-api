
from django.urls import path

from .views import (
    CertificationUploadView,
    InterpreterAvailabilityDetailView,
    InterpreterAvailabilityListCreateView,
    InterpreterProfileDetailView,
)

app_name = "interpreters"

urlpatterns = [
    path(
        "me/",
        InterpreterProfileDetailView.as_view(),
        name="interpreter-profile-detail",
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