
"""HTTP endpoints for realtime sessions and LiveKit webhooks."""

from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from livekit import api
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.realtime.lifecycle import process_livekit_event
from apps.realtime.models import SessionFeedback, VideoSession
from apps.realtime.quality import assess_connection_quality
from apps.realtime.serializers import (
    ConnectionQualityTestSerializer,
    SessionFeedbackSerializer,
    TicketSerializer,
)
from apps.realtime.tickets import TICKET_MAX_AGE_SECONDS, issue_ticket
from apps.service_requests.models import ServiceRequest, SessionInvitation


class TicketView(APIView):
    """POST /api/v1/realtime/ticket/: a one-minute ticket for opening a WebSocket."""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses={200: TicketSerializer})
    def post(self, request):
        return Response(
            TicketSerializer(
                {
                    "ticket": issue_ticket(request.user),
                    "expires_in": TICKET_MAX_AGE_SECONDS,
                }
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
                {
                    "detail": "This link is not valid.",
                    "code": "invalid_link",
                },
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
                {
                    "detail": "LiveKit webhook verification is not configured.",
                },
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
                {
                    "detail": "Invalid LiveKit webhook signature or payload.",
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        process_livekit_event(event)

        return Response(status=status.HTTP_200_OK)


class SessionFeedbackView(APIView):
    """Submit feedback about the interpreter after a completed session."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=SessionFeedbackSerializer,
        responses={201: SessionFeedbackSerializer},
    )
    @transaction.atomic
    def post(self, request, session_id):
        video_session = get_object_or_404(
            VideoSession.objects.select_for_update(of=("self",)).select_related(
                "service_request",
                "service_request__interpreter",
            ),
            pk=session_id,
        )

        service_request = video_session.service_request

        if service_request.requester_id != request.user.pk:
            return Response(
                {
                    "detail": "Only the session requester can submit feedback.",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        if (
            service_request.status != ServiceRequest.Status.COMPLETED
            or video_session.status != VideoSession.Status.ENDED
        ):
            return Response(
                {
                    "detail": "Feedback can only be submitted after the session has ended.",
                },
                status=status.HTTP_409_CONFLICT,
            )

        if service_request.interpreter_id is None:
            return Response(
                {
                    "detail": "This session has no assigned interpreter.",
                },
                status=status.HTTP_409_CONFLICT,
            )

        if SessionFeedback.objects.filter(video_session=video_session).exists():
            return Response(
                {
                    "detail": "Feedback has already been submitted for this session.",
                },
                status=status.HTTP_409_CONFLICT,
            )

        serializer = SessionFeedbackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        feedback = serializer.save(
            video_session=video_session,
            interpreter=service_request.interpreter,
            submitted_by=request.user,
        )

        return Response(
            SessionFeedbackSerializer(feedback).data,
            status=status.HTTP_201_CREATED,
        )
