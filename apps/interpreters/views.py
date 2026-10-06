from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions
from rest_framework.parsers import FormParser, MultiPartParser

from .models import InterpreterProfile
from .serializers import CertificationDocumentSerializer, InterpreterProfileSerializer


@extend_schema(summary="Upload interpreter certification document")
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


@extend_schema(summary="Get or update current interpreter profile")
class InterpreterProfileDetailView(generics.RetrieveUpdateAPIView):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = InterpreterProfileSerializer

    def get_object(self):
        profile, _ = InterpreterProfile.objects.get_or_create(user=self.request.user)
        return profile
