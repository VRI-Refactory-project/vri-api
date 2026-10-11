"""Admin-only interpreter verification routes (V2-045), included under /api/v1/manage/interpreters/."""
from django.urls import path

from . import views

app_name = "manage-interpreters"

urlpatterns = [
    path("", views.ManageInterpreterListView.as_view(), name="list"),
    path("<int:pk>/", views.ManageInterpreterDetailView.as_view(), name="detail"),
    path(
        "<int:pk>/certifications/<int:cert_id>/file/",
        views.CertificationFileView.as_view(),
        name="certification-file",
    ),
]
