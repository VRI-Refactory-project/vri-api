
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.realtime.invitations import (
    create_guest_invitation,
    join_guest_invitation,
)
from apps.realtime.serializers import (
    GuestInvitationCreateSerializer,
    GuestInvitationSerializer,
    GuestJoinRequestSerializer,
    GuestJoinSerializer,
    TicketSerializer,
)
from apps.realtime.tickets import TICKET_MAX_AGE_SECONDS, issue_ticket
from apps.service_requests.models import ServiceRequest


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


class GuestInvitationCreateView(APIView):
    """Create an invitation for a hearing participant to join an accepted call."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=GuestInvitationCreateSerializer,
        responses={201: GuestInvitationSerializer, 400: None, 403: None, 404: None},
    )
    def post(self, request, session_id):
        service_request = get_object_or_404(
            ServiceRequest,
            pk=session_id,
        )

        serializer = GuestInvitationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            invitation, invitation_url = create_guest_invitation(
                service_request=service_request,
                user=request.user,
                guest_name=serializer.validated_data["guest_name"],
            )
        except PermissionError:
            return Response(
                {"detail": "You are not allowed to invite guests to this session."},
                status=status.HTTP_403_FORBIDDEN,
            )
        except ValueError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except RuntimeError:
            return Response(
                {"detail": "Video calling is not configured."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        response_data = {
            "invitation_url": invitation_url,
            "expires_at": invitation.expires_at,
        }

        return Response(
            GuestInvitationSerializer(response_data).data,
            status=status.HTTP_201_CREATED,
        )


class GuestJoinView(APIView):
    """Allow an invited guest to exchange an invitation for LiveKit credentials."""

    permission_classes = [AllowAny]

    @extend_schema(
        request=GuestJoinRequestSerializer,
        responses={
            200: GuestJoinSerializer,
            400: None,
            503: None,
        },
    )
    def post(self, request):
        serializer = GuestJoinRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            credentials = join_guest_invitation(
                serializer.validated_data["token"]
            )
        except ValueError as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except RuntimeError:
            return Response(
                {"detail": "Video calling is not configured."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            GuestJoinSerializer(credentials).data,
            status=status.HTTP_200_OK,
        )
