import os

from django.contrib.auth import get_user_model
from django.db.models import Count
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import generics, permissions
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.views import APIView

from apps.core.permissions import IsAdminRole

from .models import CertificationDocument, InterpreterProfile, VerificationStatus
from .serializers import (
    CertificationDocumentSerializer,
    InterpreterProfileSerializer,
    ManageInterpreterDetailSerializer,
    ManageInterpreterListSerializer,
)

User = get_user_model()


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


# --- V2-045: admin verification (/api/v1/manage/interpreters/) -------------------------------


def manageable_interpreters():
    """Interpreter accounts only: other roles that happen to have a profile never show up for review."""
    return InterpreterProfile.objects.filter(user__role=User.Role.INTERPRETER).select_related("user")


@extend_schema(
    tags=["Manage"],
    summary="Interpreter verification queue",
    parameters=[
        OpenApiParameter(
            "status", OpenApiTypes.STR, OpenApiParameter.QUERY, enum=VerificationStatus.values,
            description="Only interpreters with this status, e.g. INACTIVE for the pending-review queue.",
        )
    ],
)
class ManageInterpreterListView(generics.ListAPIView):
    """Interpreters for admins to review, oldest first, so the longest-waiting are reviewed first."""

    permission_classes = [IsAdminRole]
    serializer_class = ManageInterpreterListSerializer

    def get_queryset(self):
        queryset = manageable_interpreters().annotate(certification_count=Count("certifications"))
        status = self.request.query_params.get("status")
        if status:
            if status not in VerificationStatus.values:
                raise ValidationError({"status": [f"Choose one of: {', '.join(VerificationStatus.values)}."]})
            queryset = queryset.filter(verification_status=status)
        return queryset.order_by("created_at", "id")


@extend_schema(tags=["Manage"], summary="Interpreter review details (certificates and decision history)")
class ManageInterpreterDetailView(generics.RetrieveAPIView):
    permission_classes = [IsAdminRole]
    serializer_class = ManageInterpreterDetailSerializer

    def get_queryset(self):
        return manageable_interpreters().annotate(certification_count=Count("certifications")).prefetch_related(
            "certifications", "decisions__admin"
        )


class CertificationFileView(APIView):
    """Download an interpreter's certificate. Admins only: certificates are never served from a public URL."""

    permission_classes = [IsAdminRole]

    @extend_schema(
        tags=["Manage"],
        summary="Download a certification document",
        responses={
            (200, "application/octet-stream"): OpenApiTypes.BINARY,
            404: OpenApiResponse(description="No such certificate for this interpreter, or the file is missing."),
        },
    )
    def get(self, request, pk, cert_id):
        cert = get_object_or_404(
            CertificationDocument.objects.filter(interpreter__in=manageable_interpreters()), pk=cert_id,
            interpreter_id=pk,
        )
        try:
            handle = cert.document.open("rb")
        except FileNotFoundError:
            raise Http404("The file for this certificate is missing.")
        return FileResponse(handle, filename=os.path.basename(cert.document.name))
