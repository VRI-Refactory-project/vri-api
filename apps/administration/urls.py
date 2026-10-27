from django.urls import path

from apps.institutions.views import ApproveInstitutionView, RejectInstitutionView

from .views import (
    AdminChangePasswordView,
    AdminInstitutionDeactivateView,
    AdminInstitutionDetailView,
    AdminInstitutionListView,
    AdminProfileDetailView,
)

app_name = "administration"

urlpatterns = [
    path("settings/", AdminProfileDetailView.as_view(), name="admin-settings"),
    path(
        "change-password/",
        AdminChangePasswordView.as_view(),
        name="admin-change-password",
    ),
    path(
        "institutions/",
        AdminInstitutionListView.as_view(),
        name="admin-institutions",
    ),
    path(
        "institutions/<int:pk>/",
        AdminInstitutionDetailView.as_view(),
        name="admin-institution-detail",
    ),
    path(
        "institutions/<int:pk>/approve/",
        ApproveInstitutionView.as_view(),
        name="admin-institution-approve",
    ),
    path(
        "institutions/<int:pk>/reject/",
        RejectInstitutionView.as_view(),
        name="admin-institution-reject",
    ),
    path(
        "institutions/<int:pk>/deactivate/",
        AdminInstitutionDeactivateView.as_view(),
        name="admin-institution-deactivate",
    ),
]
