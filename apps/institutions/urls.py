

from django.urls import path

from .views import (
    ApproveInstitutionView,
    PendingInstitutionListView,
    RejectInstitutionView,
)

urlpatterns = [
    path("pending/", PendingInstitutionListView.as_view(), name="institution-pending"),
    path("<int:pk>/approve/", ApproveInstitutionView.as_view(), name="institution-approve"),
    path("<int:pk>/reject/", RejectInstitutionView.as_view(), name="institution-reject"),
]
