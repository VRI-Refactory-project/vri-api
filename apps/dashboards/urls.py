from django.urls import path

from . import views

urlpatterns = [
    path("deaf/", views.DeafDashboardView.as_view(), name="dashboard-deaf"),
    path(
        "deaf/available-interpreters/",
        views.AvailableInterpretersView.as_view(),
        name="dashboard-deaf-available-interpreters",
    ),
    path(
        "deaf/interpreters/<int:pk>/",
        views.InterpreterPublicProfileView.as_view(),
        name="dashboard-deaf-interpreter-profile",
    ),
    path("admin/", views.AdminDashboardView.as_view(), name="dashboard-admin"),
    path(
        "interpreter/",
        views.InterpreterDashboardView.as_view(),
        name="dashboard-interpreter",
    ),
    path(
        "institution/",
        views.InstitutionDashboardView.as_view(),
        name="dashboard-institution",
    ),
]
