
from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions
from rest_framework.parsers import FormParser, MultiPartParser

from apps.core.permissions import IsInterpreter

from .models import InterpreterAvailability, InterpreterProfile
from .serializers import (
    CertificationDocumentSerializer,
    InterpreterAvailabilitySerializer,
    InterpreterProfileSerializer,
)


@extend_schema(
    tags=["Interpreter Profile Management"],
    summary="Upload interpreter certification document",
    description=(
        "Upload a PDF or image certification document for administrative "
        "verification and onboarding (V2-003)."
    ),
)
class CertificationUploadView(generics.CreateAPIView):
    permission_classes = (permissions.IsAuthenticated,)
    parser_classes = (MultiPartParser, FormParser)
    serializer_class = CertificationDocumentSerializer

    def perform_create(self, serializer):
        profile, _ = InterpreterProfile.objects.get_or_create(
            user=self.request.user
        )
        uploaded_file = self.request.FILES.get("document")
        document_name = (
            uploaded_file.name if uploaded_file else "Certification Document"
        )
        serializer.save(
            interpreter=profile,
            document_name=document_name,
        )


@extend_schema(
    tags=["Interpreter Profile Management"],
    summary="Get or update current interpreter profile",
    description=(
        "Retrieve or update profile details for the authenticated interpreter "
        "(V2-068).\n\n"
        "**Fields supported:**\n"
        "- `specialisation`: Dropdown choice (`GENERAL`, `MEDICAL`, `LEGAL`, `OTHER`).\n"
        "- `specialisation_other`: Custom text required when `specialisation` is `OTHER`.\n"
        "- `sign_languages`: List of sign language preferences.\n"
        "- `is_available`: Boolean status toggle requiring approved verification.\n"
        "- `working_hours_schedule`: JSON schedule definition."
    ),
)
class InterpreterProfileDetailView(generics.RetrieveUpdateAPIView):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = InterpreterProfileSerializer

    def get_object(self):
        profile, _ = InterpreterProfile.objects.get_or_create(
            user=self.request.user
        )
        return profile


@extend_schema(
    summary="List or create interpreter availability periods"
)
class InterpreterAvailabilityListCreateView(generics.ListCreateAPIView):
    permission_classes = (permissions.IsAuthenticated, IsInterpreter)
    serializer_class = InterpreterAvailabilitySerializer

    def get_profile(self):
        profile, _ = InterpreterProfile.objects.get_or_create(
            user=self.request.user
        )
        return profile

    def get_queryset(self):
        return InterpreterAvailability.objects.filter(
            interpreter=self.get_profile()
        )

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["availability_profile"] = self.get_profile()
        return context

    def perform_create(self, serializer):
        serializer.save(interpreter=self.get_profile())


@extend_schema(
    summary="Retrieve, update or delete an availability period"
)
class InterpreterAvailabilityDetailView(
    generics.RetrieveUpdateDestroyAPIView
):
    permission_classes = (permissions.IsAuthenticated, IsInterpreter)
    serializer_class = InterpreterAvailabilitySerializer

    def get_queryset(self):
        return InterpreterAvailability.objects.filter(
            interpreter__user=self.request.user
        )

    def get_serializer_context(self):
        context = super().get_serializer_context()
        profile, _ = InterpreterProfile.objects.get_or_create(
            user=self.request.user
        )
        context["availability_profile"] = profile
        return context
