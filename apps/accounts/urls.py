from django.urls import path

from . import views

urlpatterns = [
    path("health/", views.health, name="health"),
    path("auth/roles/", views.roles, name="auth-roles"),
    path("auth/login/", views.LoginView.as_view(), name="auth-login"),
    path("auth/token/refresh/", views.RefreshView.as_view(), name="auth-token-refresh"),
    path("auth/me/", views.MeView.as_view(), name="auth-me"),
]
