
from django.urls import path

from . import views
from .views import (
    ApproveInstitutionView,
    InstitutionalAccountUpdateAPIView,
    PendingInstitutionListView,
    RejectInstitutionView,
)

urlpatterns = [
    path("pending/", PendingInstitutionListView.as_view(), name="institution-pending"),
    path("<int:pk>/approve/", ApproveInstitutionView.as_view(), name="institution-approve"),
    path("<int:pk>/reject/", RejectInstitutionView.as_view(), name="institution-reject"),
    path('api/institution/account/', InstitutionalAccountUpdateAPIView.as_view(), name='institution-account-update'),
    path("me/", views.MyInstitutionView.as_view(), name="institution-me"),
    # V2-006: staff accounts, managed by the institution's administrators
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
    path("pending/", PendingInstitutionListView.as_view(), name="institution-pending"),
    path(
        "<int:pk>/approve/",
        ApproveInstitutionView.as_view(),
        name="institution-approve",
    ),
    path(
        "<int:pk>/reject/", RejectInstitutionView.as_view(), name="institution-reject"
    ),
]
