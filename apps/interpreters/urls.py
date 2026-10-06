from django.urls import path
from .views import CertificationUploadView, InterpreterProfileDetailView

app_name = "interpreters"

urlpatterns = [
    path(
        "me/", InterpreterProfileDetailView.as_view(), name="interpreter-profile-detail"
    ),
    path(
        "certifications/upload/",
        CertificationUploadView.as_view(),
        name="certification-upload",
    ),
]
