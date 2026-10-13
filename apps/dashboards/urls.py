from django.urls import path

from . import views

urlpatterns = [
    path("deaf/", views.DeafDashboardView.as_view(), name="dashboard-deaf"),
    path("admin/", views.AdminDashboardView.as_view(), name="dashboard-admin"),
    path("interpreter/",views.InterpreterDashboardView.as_view(),name="dashboard-interpreter",),
]
