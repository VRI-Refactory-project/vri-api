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
    path("<int:pk>/approve/", views.ApproveInterpreterView.as_view(), name="approve"),
    path("<int:pk>/reject/", views.RejectInterpreterView.as_view(), name="reject"),
    path("<int:pk>/deactivate/", views.DeactivateInterpreterView.as_view(), name="deactivate"),
    path("<int:pk>/reactivate/", views.ReactivateInterpreterView.as_view(), name="reactivate"),
]
