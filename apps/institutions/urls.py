from django.urls import path

from . import views
from .views import (
    ApproveInstitutionView,
    DeleteInstitutionView,
    InstitutionalAccountUpdateAPIView,
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
        "me/account/",
        InstitutionalAccountUpdateAPIView.as_view(),
        name="institution-account-update"
    ),
    # Staff management routes
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
]
