from django.urls import path

from .views import AdminChangePasswordView, AdminProfileDetailView

app_name = "administration"

urlpatterns = [
    path("settings/", AdminProfileDetailView.as_view(), name="admin-settings"),
    path(
        "change-password/",
        AdminChangePasswordView.as_view(),
        name="admin-change-password",
    ),
]
