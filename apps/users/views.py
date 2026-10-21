from django.http import Http404, HttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import ProfilePhoto, UserProfile
from .photos import PHOTO_URL_PREFIX, InvalidPhoto, process_upload
from .serializers import UserProfileSerializer, UserRegistrationSerializer


@extend_schema(
    tags=["User Management"],
    summary="User Registration",
    description=(
        "Registers a new web user account using email, "
        "password, and optional contact number."
    ),
)
class UserRegistrationView(generics.CreateAPIView):
    permission_classes = [permissions.AllowAny]
    serializer_class = UserRegistrationSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(
            {
                "message": "User registered successfully.",
                "email": user.email,
                "contact_number": getattr(user.profile, "contact_number", None),
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(
    tags=["User Management"],
    summary="Deaf User Profile Management",
    description=(
        "Retrieve or update details for the authenticated Deaf user, "
        "including emergency contact information, sign language "
        "preferences, and sensory accessibility settings (V2-001 / FR-UM-003)."
    ),
)
class DeafUserProfileDetailView(generics.RetrieveUpdateAPIView):
    """Handles GET, PUT, and PATCH requests for viewing and updating

    Deaf user profile details, emergency contact info, and accessibility
    preferences.
    """

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = UserProfileSerializer

    def get_object(self):
        # Automatically fetch or create UserProfile for the logged-in user
        profile, _ = UserProfile.objects.get_or_create(user=self.request.user)

        # Copy registered phone number if profile contact number is missing
        if not profile.contact_number and getattr(
            self.request.user, "phone_number", None
        ):
            profile.contact_number = str(self.request.user.phone_number)
            profile.save(update_fields=["contact_number"])

        return profile

    def partial_update(self, request, *args, **kwargs):
        profile = self.get_object()
        serializer = self.get_serializer(
            profile,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)


class MyPhotoView(APIView):
    """GET, POST or DELETE the signed-in user's profile photo (any role).

    POST takes a multipart form with the picture in a field called "photo".
    """

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(tags=["User Management"], summary="Get my profile photo link")
    def get(self, request):
        # Read the database directly: the user object may remember a photo that was just removed.
        token = (
            ProfilePhoto.objects.filter(user=request.user).values_list("token", flat=True).first()
        )
        return Response({"profile_photo": f"{PHOTO_URL_PREFIX}{token}/" if token else None})

    @extend_schema(tags=["User Management"], summary="Upload my profile photo")
    def post(self, request):
        upload = request.FILES.get("photo")
        if upload is None:
            return Response(
                {"photo": ["Choose a picture to upload."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            data = process_upload(upload)
        except InvalidPhoto as error:
            return Response({"photo": [str(error)]}, status=status.HTTP_400_BAD_REQUEST)

        photo, _ = ProfilePhoto.objects.get_or_create(
            user=request.user, defaults={"image": data}
        )
        # A new token for every upload, so browsers never show an old picture from their cache.
        photo.image = data
        photo.content_type = "image/jpeg"
        photo.token = ProfilePhoto._meta.get_field("token").default()
        photo.save()
        return Response({"profile_photo": f"{PHOTO_URL_PREFIX}{photo.token}/"})

    @extend_schema(tags=["User Management"], summary="Remove my profile photo")
    def delete(self, request):
        ProfilePhoto.objects.filter(user=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PhotoImageView(APIView):
    """GET /api/v1/auth/photos/<token>/: the picture itself, for <img> tags."""

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    @extend_schema(tags=["User Management"], summary="A profile photo", responses={200: bytes})
    def get(self, request, token):
        photo = ProfilePhoto.objects.filter(token=token).first()
        if photo is None:
            raise Http404
        response = HttpResponse(bytes(photo.image), content_type=photo.content_type)
        # The token changes with every upload, so a cached copy can never be out of date.
        response["Cache-Control"] = "public, max-age=604800, immutable"
        return response
