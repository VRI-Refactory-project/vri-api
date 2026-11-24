from django.urls import path

from apps.institutions.views import InstitutionUsageExportView

from apps.administration import support_requests
from . import admin_stats, views

urlpatterns = [
    path(
        "admin/support-requests/",
        support_requests.AdminSupportRequestListView.as_view(),
        name="dashboard-admin-support-requests",
    ),
    path(
        "admin/support-requests/<int:pk>/",
        support_requests.AdminSupportRequestDetailView.as_view(),
        name="dashboard-admin-support-request-detail",
    ),
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
        "admin/interpreter-performance/",
        admin_stats.AdminInterpreterPerformanceView.as_view(),
        name="dashboard-admin-interpreter-performance",
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
    # The usage PDF lives with the usage figures it is built from (apps/institutions); the web app
    # already downloads it from this address.
    path(
        "institution/export/",
        InstitutionUsageExportView.as_view(),
        name="dashboard-institution-export",
    ),
    path(
        "admin/sessions/",
        views.AdminLiveSessionsView.as_view(),
        name="admin-live-sessions",
    ),
    path(
        "admin/sessions-stats/",
        views.AdminSessionStatsView.as_view(),
        name="admin-session-stats",
    ),
    path(
        "admin/audit-logs/",
        views.AdminAuditLogsView.as_view(),
        name="dashboard-admin-audit-logs",
    ),
]
