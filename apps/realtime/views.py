
"""HTTP endpoints for realtime sessions and LiveKit webhooks."""

from django.conf import settings
from drf_spectacular.utils import extend_schema
from livekit import api
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.realtime.lifecycle import process_livekit_event
from apps.realtime.quality import assess_connection_quality
from apps.realtime.serializers import (
    ConnectionQualityTestSerializer,
    TicketSerializer,
)
from apps.realtime.tickets import TICKET_MAX_AGE_SECONDS, issue_ticket
from apps.service_requests.models import SessionInvitation


class TicketView(APIView):
    """POST /api/v1/realtime/ticket/: a one-minute ticket for opening a WebSocket."""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses={200: TicketSerializer})
    def post(self, request):
        return Response(
            TicketSerializer(
                {"ticket": issue_ticket(request.user), "expires_in": TICKET_MAX_AGE_SECONDS}
            ).data
        )


class ConnectionQualityTestView(APIView):
    """Assess a signed-in participant's connection before joining a session."""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=ConnectionQualityTestSerializer)
    def post(self, request):
        serializer = ConnectionQualityTestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = assess_connection_quality(serializer.validated_data)
        return Response(result, status=status.HTTP_200_OK)


class GuestConnectionQualityTestView(APIView):
    """Assess an invited guest's connection before joining a session."""

    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(request=ConnectionQualityTestSerializer)
    def post(self, request, token):
        invitation = SessionInvitation.objects.filter(token=token).first()

        if invitation is None:
            return Response(
                {"detail": "This link is not valid.", "code": "invalid_link"},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not invitation.is_open:
            return Response(
                {
                    "detail": "This session has ended or you were removed.",
                    "code": "link_closed",
                },
                status=status.HTTP_410_GONE,
            )

        serializer = ConnectionQualityTestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = assess_connection_quality(serializer.validated_data)
        return Response(result, status=status.HTTP_200_OK)


class LiveKitWebhookView(APIView):
    """Receive and verify server-to-server webhook events from LiveKit."""

    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(exclude=True)
    def post(self, request):
        if not settings.LIVEKIT_API_KEY or not settings.LIVEKIT_API_SECRET:
            return Response(
                {"detail": "LiveKit webhook verification is not configured."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        authorization = request.headers.get("Authorization", "")
        if not authorization:
            return Response(
                {"detail": "Missing LiveKit authorization header."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        try:
            body = request.body.decode("utf-8")
            receiver = api.WebhookReceiver(
                api.TokenVerifier(
                    settings.LIVEKIT_API_KEY,
                    settings.LIVEKIT_API_SECRET,
                )
            )
            event = receiver.receive(body, authorization)
        except Exception:
            return Response(
                {"detail": "Invalid LiveKit webhook signature or payload."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        process_livekit_event(event)

        return Response(status=status.HTTP_200_OK)
