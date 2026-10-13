from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions
from rest_framework.parsers import FormParser, MultiPartParser

from .models import InterpreterProfile
from .serializers import (
    CertificationDocumentSerializer,
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
        profile, _ = InterpreterProfile.objects.get_or_create(user=self.request.user)
        uploaded_file = self.request.FILES.get("document")
        document_name = (
            uploaded_file.name if uploaded_file else "Certification Document"
        )
        serializer.save(interpreter=profile, document_name=document_name)


@extend_schema(
    tags=["Interpreter Profile Management"],
    summary="Get or update current interpreter profile",
    description=(
        "Retrieve or update profile details for the authenticated interpreter (V2-068).\n\n"
        "**Fields supported:**\n"
        "- `specialisation`: Dropdown choice (`GENERAL`, `MEDICAL`, `LEGAL`, `OTHER`).\n"
        "- `specialisation_other`: Custom text required when `specialisation` is set to `OTHER`.\n"
        "- `sign_languages`: List of sign language preferences (e.g. `['Ugandan Sign Language']`).\n"
        "- `is_available`: Boolean status toggle (requires `APPROVED` verification status).\n"
        "- `working_hours_schedule`: JSON schedule definition."
    ),
)
class InterpreterProfileDetailView(generics.RetrieveUpdateAPIView):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = InterpreterProfileSerializer

    def get_object(self):
        profile, _ = InterpreterProfile.objects.get_or_create(user=self.request.user)
        return profile
