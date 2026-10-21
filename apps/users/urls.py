from django.urls import path

from .views import (
    DeafUserProfileDetailView,
    MyPhotoView,
    PhotoImageView,
    UserRegistrationView,
)

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
    path("me/photo/", MyPhotoView.as_view(), name="my-photo"),
    # The deaf profile page already calls this path.
    path("deaf-user/me/photo/", MyPhotoView.as_view(), name="deaf-user-photo"),
    path("photos/<str:token>/", PhotoImageView.as_view(), name="photo-image"),
]
