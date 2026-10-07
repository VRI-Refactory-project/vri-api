from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, status
from rest_framework.response import Response

from .serializers import UserRegistrationSerializer


@extend_schema(summary="Register a new web user account")
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
