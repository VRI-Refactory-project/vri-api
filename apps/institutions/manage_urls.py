from django.urls import path

from . import manage_views

urlpatterns = [
    path("", manage_views.ManageInstitutionCreateView.as_view(), name="manage-institution-create"),
]
