


from django.urls import path

from .views import (
    ApproveInstitutionView,
    PendingInstitutionListView,
    RejectInstitutionView,InstitutionalAccountUpdateAPIView
)

urlpatterns = [
    path("pending/", PendingInstitutionListView.as_view(), name="institution-pending"),
    path("<int:pk>/approve/", ApproveInstitutionView.as_view(), name="institution-approve"),
    path("<int:pk>/reject/", RejectInstitutionView.as_view(), name="institution-reject"),
    path('api/institution/account/', InstitutionalAccountUpdateAPIView.as_view(), name='institution-account-update'),
]
