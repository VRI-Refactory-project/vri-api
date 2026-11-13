from django.urls import path

from . import admin_stats, views

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
        "admin/logout/", views.AdminLogoutView.as_view(), name="dashboard-admin-logout"
    ),
    path(
        "admin/upcoming-sessions/",
        views.AdminUpcomingSessionsView.as_view(),
        name="dashboard-admin-upcoming-sessions",
    ),
    path(
        "admin/user-growth/",
        admin_stats.AdminUserGrowthView.as_view(),
        name="dashboard-admin-user-growth",
    ),
    path(
        "admin/sessions-activity/",
        admin_stats.AdminSessionsActivityView.as_view(),
        name="dashboard-admin-sessions-activity",
    ),
    path(
        "admin/session-types/",
        admin_stats.AdminSessionTypesView.as_view(),
        name="dashboard-admin-session-types",
    ),
    path(
        "admin/verification-status/",
        admin_stats.AdminVerificationStatusView.as_view(),
        name="dashboard-admin-verification-status",
    ),
    path(
        "admin/activity/",
        admin_stats.AdminActivityFeedView.as_view(),
        name="dashboard-admin-activity",
    ),
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