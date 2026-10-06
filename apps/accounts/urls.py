from django.urls import path

from . import views

urlpatterns = [
    path("health/", views.health, name="health"),
    path("auth/login/", views.LoginView.as_view(), name="auth-login"),
    path("auth/token/refresh/", views.RefreshView.as_view(), name="auth-token-refresh"),
    path("auth/me/", views.MeView.as_view(), name="auth-me"),
    path("auth/password/change/", views.PasswordChangeView.as_view(), name="auth-password-change"),
    # V2-008 account recovery
    path("auth/recovery/request/", views.RecoveryRequestView.as_view(), name="auth-recovery-request"),
    path("auth/recovery/confirm/", views.RecoveryConfirmView.as_view(), name="auth-recovery-confirm"),
    path("manage/users/<int:pk>/reset-password/request/", views.AdminResetRequestView.as_view(),
         name="manage-reset-password-request"),
    path("manage/users/<int:pk>/reset-password/confirm/", views.AdminResetConfirmView.as_view(),
         name="manage-reset-password-confirm"),
]
