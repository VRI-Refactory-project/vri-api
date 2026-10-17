from django.urls import path

from .views import DeafUserProfileDetailView, UserRegistrationView

urlpatterns = [
    path(
        "register/",
        UserRegistrationView.as_view(),
        name="user-register",
    ),
    path(
        "deaf-user/me/",
        DeafUserProfileDetailView.as_view(),
        name="deaf-user-profile-me",
    ),
]
