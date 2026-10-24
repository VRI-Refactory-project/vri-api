from django.urls import path

from .views import (
    AdminChangePasswordView,
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
]
