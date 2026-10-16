

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.generics import RetrieveAPIView
from rest_framework import serializers, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from drf_spectacular.utils import extend_schema
from apps.audit import services as audit
from apps.audit.models import AuditLog
from apps.core.permissions import IsAdminRole
from apps.core.permissions import IsApprovedInstitution, user_institution
from apps.core.permissions import HasInstitutionalRole
from .serializers import InstitutionSerializer,InstitutionProfileUpdateSerializer


from .models import InstitutionProfile, InstitutionStatus, InstitutionStaff

logger = logging.getLogger(__name__)


class InstitutionApplicationSerializer(serializers.ModelSerializer):
    administrator_name = serializers.CharField(
        source="user.full_name", read_only=True
    )
    email = serializers.EmailField(source="user.email", read_only=True)
    phone_number = serializers.CharField(
        source="user.phone_number", read_only=True
    )

    class Meta:
        model = InstitutionProfile
        fields = [
            "id",
            "organisation_name",
            "organisation_type",
            "organisation_type_other",
            "status",
            "administrator_name",
            "email",
            "phone_number",
            "created_at",
        ]


def notify_institution(profile, decision):
    """Attempt to email the institution administrator about the decision."""
    if not profile.user.email:
        return

    approved = decision == InstitutionStatus.APPROVED
    subject = (
        "Institution account approved"
        if approved
        else "Institution account application rejected"
    )
    message = (
        f"Hello {profile.user.full_name},\n\n"
        f"Your application for {profile.organisation_name} "
        f"has been {'approved' if approved else 'rejected'}.\n"
    )
    try:
        send_mail(
            subject,
            message,
            getattr(settings, "DEFAULT_FROM_EMAIL", None),
            [profile.user.email],
            fail_silently=False,
        )
    except Exception:
        # A mail delivery problem must not undo the administrator's decision.
        logger.exception(
            "Could not send institution decision notification; institution_id=%s",
            profile.pk,
        )


class PendingInstitutionListView(APIView):
    permission_classes = [IsAdminRole]

    @extend_schema(
        tags=["Institutions"],
        summary="List institution applications awaiting approval",
        responses={200: InstitutionApplicationSerializer(many=True)},
    )
    def get(self, request):
        applications = InstitutionProfile.objects.filter(
            status=InstitutionStatus.PENDING
        ).select_related("user")
        return Response(InstitutionApplicationSerializer(applications, many=True).data)


class InstitutionDecisionView(APIView):
    permission_classes = [IsAdminRole]
    decision = None
    audit_event = None

    def post(self, request, pk):
        with transaction.atomic():
            profile = get_object_or_404(
                InstitutionProfile.objects.select_for_update().select_related("user"),
                pk=pk,
            )

            if profile.status != InstitutionStatus.PENDING:
                raise ValidationError({
                    "status": [
                        "Only pending institution applications can be approved or rejected."
                    ]
                })

            profile.status = self.decision
            profile.updated_at = timezone.now()
            profile.save(update_fields=["status", "updated_at"])

            audit.record(self.audit_event, request.user, request)

            transaction.on_commit(
                lambda: notify_institution(profile, self.decision)
            )

        return Response(
            InstitutionApplicationSerializer(profile).data,
            status=status.HTTP_200_OK,
        )


class ApproveInstitutionView(InstitutionDecisionView):
    decision = InstitutionStatus.APPROVED
    audit_event = AuditLog.Event.INSTITUTION_APPROVED

    @extend_schema(
        tags=["Institutions"],
        summary="Approve a pending institution application",
        request=None,
        responses={200: InstitutionApplicationSerializer},
    )
    def post(self, request, pk):
        return super().post(request, pk)


class RejectInstitutionView(InstitutionDecisionView):
    decision = InstitutionStatus.REJECTED
    audit_event = AuditLog.Event.INSTITUTION_REJECTED

    @extend_schema(
        tags=["Institutions"],
        summary="Reject a pending institution application",
        request=None,
        responses={200: InstitutionApplicationSerializer},
    )
    def post(self, request, pk):
        return super().post(request, pk)

    
class MyInstitutionView(RetrieveAPIView):
    """The signed-in user's own institution. There is no way to ask for another institution by id."""

    permission_classes = [IsApprovedInstitution]
    serializer_class = InstitutionSerializer

    def get_object(self):
        return user_institution(self.request.user)

class InstitutionalAccountUpdateAPIView(APIView):
    """
    Endpoint for institutional managers to update their organization's account details.
    """
    permission_classes = [IsAuthenticated, HasInstitutionalRole]
    

    def get_object(self, request):
        return get_object_or_404(
            InstitutionProfile.objects.filter(
                user=request.user,
                status=InstitutionStatus.APPROVED,
            )
        )
    def get(self, request, *args, **kwargs):
        """Retrieve the current account settings profile configuration."""
        institution = self.get_object(request)
        serializer = InstitutionProfileUpdateSerializer(institution)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def patch(self, request, *args, **kwargs):
        """Partially update account configuration properties dynamically."""
        institution = self.get_object(request)
        
        # Pass partial=True so the frontend can send only the fields that changed
        serializer = InstitutionProfileUpdateSerializer(institution, data=request.data, partial=True)
        
        if serializer.is_valid():
            serializer.save()
            return Response({
                "message": "Institutional settings updated successfully.",
                "updated_data": serializer.data
            }, status=status.HTTP_200_OK)
            
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class InstitutionalDashboardView(APIView):
    permission_classes = [IsAuthenticated, HasInstitutionalRole]

    def get(self, request):
        return Response({"message": "Institutional access granted."})

