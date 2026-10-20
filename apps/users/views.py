from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, status
from rest_framework.response import Response

from .models import UserProfile
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
