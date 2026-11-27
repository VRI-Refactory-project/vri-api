from django.urls import path

from apps.institutions.views import ApproveInstitutionView, RejectInstitutionView

from .user_list import (
    AdminUserActivateView,
    AdminUserApproveView,
    AdminUserDeactivateView,
    AdminUserDeleteView,
    AdminUserDetailView,
    AdminUserListView,
)
from .views import (
    AdminChangePasswordView,
    AdminInstitutionActivateView,
    AdminInstitutionDeactivateView,
    AdminInstitutionDetailView,
    AdminInstitutionDocumentDownloadView,
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
    path("users/", AdminUserListView.as_view(), name="admin-users"),
    path("users/<int:pk>/approve/", AdminUserApproveView.as_view(), name="admin-user-approve"),
    path("users/<int:pk>/activate/", AdminUserActivateView.as_view(), name="admin-user-activate"),
    path("users/<int:pk>/deactivate/", AdminUserDeactivateView.as_view(), name="admin-user-deactivate"),
    path("users/<int:pk>/delete/", AdminUserDeleteView.as_view(), name="admin-user-delete"),
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
        "institutions/<int:pk>/documents/<int:document_pk>/download/",
        AdminInstitutionDocumentDownloadView.as_view(),
        name="admin-institution-document-download",
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
    path(
        "institutions/<int:pk>/activate/",
        AdminInstitutionActivateView.as_view(),
        name="admin-institution-activate",
    ),
    path("users/<int:pk>/", AdminUserDetailView.as_view(), name="admin-user-detail"),
]
