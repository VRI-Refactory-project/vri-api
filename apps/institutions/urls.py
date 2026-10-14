from django.urls import path

from . import views

urlpatterns = [
    path("me/", views.MyInstitutionView.as_view(), name="institution-me"),
]
