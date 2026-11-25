from django.urls import path

from . import views
from .views import (
    ApproveInstitutionView,
    DeleteInstitutionView,
    InstitutionalAccountUpdateAPIView,
    InstitutionDocumentUploadView,
    PendingInstitutionListView,
    RejectInstitutionView,
)

urlpatterns = [
    # Admin / Management routes
    path("pending/", PendingInstitutionListView.as_view(), name="institution-pending"),
    path(
        "<int:pk>/approve/",
        ApproveInstitutionView.as_view(),
        name="institution-approve",
    ),
    path(
        "<int:pk>/reject/", RejectInstitutionView.as_view(), name="institution-reject"
    ),
    path(
        "<int:pk>/delete/",
        DeleteInstitutionView.as_view(),
        name="institution-delete",
    ),

    # Institution Administrator routes (Self-service)
    path("me/", views.MyInstitutionView.as_view(), name="institution-me"),
    path(
        "me/notices/",
        views.InstitutionNoticeListCreateView.as_view(),
        name="institution-notices",
    ),
    path(
        "me/notices/<int:pk>/",
        views.InstitutionNoticeDetailView.as_view(),
        name="institution-notice-detail",
    ),
    path(
        "me/institution-profile/",
        views.MyInstitutionProfileView.as_view(),
        name="institution-profile-me",
    ),
    path(
        "me/departments/",
        views.InstitutionDepartmentListCreateView.as_view(),
        name="institution-departments",
    ),
    path(
        "me/departments/<int:pk>/",
        views.InstitutionDepartmentDetailView.as_view(),
        name="institution-department-detail",
    ),
    path(
        "me/account/",
        InstitutionalAccountUpdateAPIView.as_view(),
        name="institution-account-update",
    ),

    # Staff management routes
    path(
        "staff/invitations/accept/",
        views.StaffInvitationAcceptView.as_view(),
        name="institution-staff-invitation-accept",
    ),
    path("me/staff/", views.StaffListCreateView.as_view(), name="institution-staff"),
    path(
        "me/staff/<int:pk>/",
        views.StaffDetailView.as_view(),
        name="institution-staff-detail",
    ),
    path(
        "me/staff/<int:pk>/revoke/",
        views.StaffRevokeView.as_view(),
        name="institution-staff-revoke",
    ),
    path(
        "me/staff/<int:pk>/reinstate/",
        views.StaffReinstateView.as_view(),
        name="institution-staff-reinstate",
    ),
    path(
        "me/profile/",
        views.MyStaffProfileView.as_view(),
        name="institution-my-profile",
    ),
    path(
        "me/sessions/",
        views.InstitutionSessionListView.as_view(),
        name="institution-sessions",
    ),
    path(
        "me/sessions/<int:pk>/",
        views.InstitutionSessionDetailView.as_view(),
        name="institution-session-detail",
    ),
    path(
        "me/tutorial/",
        views.StaffTutorialView.as_view(),
        name="institution-staff-tutorial",
    ),
    path(
        "me/scheduled-sessions/",
        views.InstitutionScheduledSessionListView.as_view(),
        name="institution-scheduled-sessions",
    ),
    path(
        "me/usage/",
        views.InstitutionUsageView.as_view(),
        name="institution-usage",
    ),
    path(
        "me/alerts/",
        views.InstitutionAlertListView.as_view(),
        name="institution-alerts",
    ),
    path(
        "me/alerts/<int:pk>/read/",
        views.InstitutionAlertReadView.as_view(),
        name="institution-alert-read",
    ),
    path(
        "me/document/",
        InstitutionDocumentUploadView.as_view(),
        name="institution-document-upload",
    ),
]
