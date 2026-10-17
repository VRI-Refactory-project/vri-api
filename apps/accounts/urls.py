from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views
from .views import AvailabilityOverrideViewSet, BaseAvailabilityViewSet

router = DefaultRouter()
router.register(r'availability/base', BaseAvailabilityViewSet, basename='base-availability')
router.register(r'availability/overrides', AvailabilityOverrideViewSet, basename='availability-override')

urlpatterns = [
    path("health/", views.health, name="health"),
    path("auth/roles/", views.roles, name="auth-roles"),
    path("auth/login/", views.LoginView.as_view(), name="auth-login"),
    path("auth/token/refresh/", views.RefreshView.as_view(), name="auth-token-refresh"),
    path("auth/me/", views.MeView.as_view(), name="auth-me"),
    path("auth/me/access/<str:role>/", views.RoleAccessView.as_view(), name="auth-me-access"),
    path("auth/password/change/", views.PasswordChangeView.as_view(), name="auth-password-change"),
    # V2-001 / V2-003 registration (signs the user in: returns access, refresh, user)
    path("auth/register/deaf/", views.DeafRegisterView.as_view(), name="auth-register-deaf"),
    path("auth/register/interpreter/", views.InterpreterRegisterView.as_view(),
         name="auth-register-interpreter"),
    path("auth/register/institution/", views.InstitutionRegisterView.as_view(),
         name="auth-register-institution"),
    # Email verification (signed-in user)
    path("auth/email/verify/", views.EmailVerifyConfirmView.as_view(), name="auth-email-verify"),
    path("auth/email/resend/", views.EmailVerifyResendView.as_view(), name="auth-email-resend"),
    # V2-008 account recovery
    path("auth/recovery/request/", views.RecoveryRequestView.as_view(), name="auth-recovery-request"),
    path("auth/recovery/confirm/", views.RecoveryConfirmView.as_view(), name="auth-recovery-confirm"),
    path("manage/users/<int:pk>/reset-password/request/", views.AdminResetRequestView.as_view(),
         name="manage-reset-password-request"),
    path("manage/users/<int:pk>/reset-password/confirm/", views.AdminResetConfirmView.as_view(),
         name="manage-reset-password-confirm"),
    path('api/', include(router.urls)),
]
