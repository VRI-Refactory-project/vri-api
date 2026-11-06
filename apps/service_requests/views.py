import logging

from django.conf import settings
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsApprovedInstitution, IsDeafUser, IsInterpreter
from apps.service_requests.call_tokens import (
    create_call_token,
    create_guest_token,
    interpreter_is_in_room,
    remove_guest_from_room,
    room_name,
)
from apps.service_requests.invitations import send_invitation_email
from apps.service_requests.models import (
    OPEN_STATUSES,
    RequestOffer,
    ServiceRequest,
    SessionInvitation,
)
from apps.service_requests.serializers import (
    OFFER_STATUS_FOR_SCREEN,
    CallTokenSerializer,
    CreateInvitationSerializer,
    InterpreterOfferSerializer,
    InvitationSerializer,
    OfferedRequestSerializer,
    OfferResponseSerializer,
    OfferStatusSerializer,
    ServiceRequestSerializer,
)
from apps.service_requests.services import (
    NotCancellable,
    OfferNotActive,
    OpenRequestExists,
    cancel_request,
    create_request,
    expire_stale_offers,
    refresh_for_requester,
    respond_to_offer,
)

logger = logging.getLogger(__name__)

# Who may ask for an interpreter (V2-024): Deaf users, and staff of an approved institution.
# Every other role gets 403; pending or rejected institutions get a 403 that says why.
CAN_REQUEST = [IsAuthenticated, IsDeafUser | IsApprovedInstitution]


class ServiceRequestListCreateView(generics.ListCreateAPIView):
    """GET /api/v1/requests/ lists your requests. POST asks for an interpreter."""

    serializer_class = ServiceRequestSerializer
    permission_classes = CAN_REQUEST

    def get_queryset(self):
        return self.request.user.service_requests.select_related(
            "requester", "interpreter", "interpreter__user"
        ).all()

    def list(self, request, *args, **kwargs):
        # Reading counts as "still here" and applies any timeout that is due.
        for open_request in self.get_queryset().filter(status__in=OPEN_STATUSES):
            refresh_for_requester(open_request)
        return super().list(request, *args, **kwargs)

    @extend_schema(responses={201: ServiceRequestSerializer, 409: None})
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            service_request, created = create_request(
                requester=request.user, request=request, **serializer.validated_data
            )
        except OpenRequestExists:
            open_request = ServiceRequest.objects.filter(
                requester=request.user, status__in=OPEN_STATUSES
            ).first()
            return Response(
                {
                    "detail": "You already have an open request.",
                    "code": "open_request_exists",
                    "open_request_id": open_request.pk if open_request else None,
                },
                status=status.HTTP_409_CONFLICT,
            )
        return Response(
            ServiceRequestSerializer(service_request).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class MyOpenRequestView(APIView):
    """GET /api/v1/requests/mine/: the request still looking for an interpreter, so the app can
    pick it up again after being closed. 204 when there is none."""

    permission_classes = CAN_REQUEST

    @extend_schema(responses={200: ServiceRequestSerializer, 204: None})
    def get(self, request):
        open_request = ServiceRequest.objects.filter(
            requester=request.user, status__in=OPEN_STATUSES
        ).first()
        if open_request is None:
            return Response(status=status.HTTP_204_NO_CONTENT)
        open_request = refresh_for_requester(open_request)
        if open_request.status not in OPEN_STATUSES:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(ServiceRequestSerializer(open_request).data)


class ServiceRequestDetailView(APIView):
    """GET /api/v1/requests/<id>/: where the request is now. Asking also counts as "still here"."""

    permission_classes = CAN_REQUEST

    @extend_schema(responses={200: ServiceRequestSerializer})
    def get(self, request, pk):
        found = get_object_or_404(ServiceRequest, pk=pk, requester=request.user)
        found = refresh_for_requester(found)
        return Response(ServiceRequestSerializer(found).data)


class ServiceRequestCancelView(APIView):
    """POST /api/v1/requests/<id>/cancel/"""

    permission_classes = CAN_REQUEST

    @extend_schema(request=None, responses={200: ServiceRequestSerializer, 409: None})
    def post(self, request, pk):
        found = get_object_or_404(ServiceRequest, pk=pk, requester=request.user)
        try:
            cancelled = cancel_request(found, request=request)
        except NotCancellable:
            return Response(
                {
                    "detail": "This request can no longer be cancelled.",
                    "code": "not_cancellable",
                },
                status=status.HTTP_409_CONFLICT,
            )
        return Response(ServiceRequestSerializer(cancelled).data)


class ServiceRequestCallTokenView(APIView):
    """POST /api/v1/requests/<id>/call-token/: lets a session's participant join its call."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=None, responses={200: CallTokenSerializer, 404: None, 409: None}
    )
    def post(self, request, pk):
        # Anyone who is not on this session gets 404, so they cannot tell it exists.
        found = get_object_or_404(
            ServiceRequest.objects.filter(
                Q(requester=request.user)
                | Q(interpreter__user=request.user)
                | Q(
                    offers__interpreter__user=request.user,
                    offers__outcome=RequestOffer.Outcome.PENDING,
                )
            ).distinct(),
            pk=pk,
        )
        # An interpreter who holds an open offer for the request also matches above (broadcast offers).
        if found.status != ServiceRequest.Status.ASSIGNED:
            return Response(
                {
                    "detail": "This session is not ready to join.",
                    "code": "session_not_ready",
                },
                status=status.HTTP_409_CONFLICT,
            )
        return Response(
            CallTokenSerializer(
                {
                    "token": create_call_token(found, request.user),
                    "url": settings.LIVEKIT_URL,
                    "room": room_name(found),
                }
            ).data
        )


class InterpreterOffersView(APIView):
    """GET /api/v1/interpreters/me/requests/: requests currently offered to the signed-in interpreter."""

    permission_classes = [IsAuthenticated, IsInterpreter]

    @extend_schema(responses={200: OfferedRequestSerializer(many=True)})
    def get(self, request):
        expire_stale_offers(interpreter_user=request.user)
        # Offers are open to several interpreters at once, so a request is offered to this person
        # when one of its offers is still waiting for them.
        offered = (
            ServiceRequest.objects.filter(
                status=ServiceRequest.Status.OFFERED,
                offers__interpreter__user=request.user,
                offers__outcome=RequestOffer.Outcome.PENDING,
            )
            .select_related("requester")
            .distinct()
        )
        return Response(
            OfferedRequestSerializer(
                offered, many=True, context={"user": request.user}
            ).data
        )


class InterpreterOfferListView(APIView):
    """GET /api/v1/interpreters/me/offers/: the offers waiting for the signed-in interpreter's answer
    (V2-010). Expired offers are taken back first, so they never show."""

    permission_classes = [IsAuthenticated, IsInterpreter]

    @extend_schema(responses={200: InterpreterOfferSerializer(many=True)})
    def get(self, request):
        expire_stale_offers(interpreter_user=request.user)
        offers = RequestOffer.objects.filter(
            interpreter__user=request.user,
            outcome=RequestOffer.Outcome.PENDING,
            service_request__status=ServiceRequest.Status.OFFERED,
            expires_at__gt=timezone.now(),
        ).select_related("service_request__requester")
        return Response(InterpreterOfferSerializer(offers, many=True).data)


class InterpreterOfferRespondView(APIView):
    """POST /api/v1/interpreters/me/offers/<id>/respond/ with {"action": "accept" | "decline"} (V2-010)."""

    permission_classes = [IsAuthenticated, IsInterpreter]

    @extend_schema(
        request=OfferResponseSerializer,
        responses={
            200: OfferStatusSerializer,
            409: OpenApiResponse(
                description="The offer expired, was withdrawn, or was already answered."
            ),
        },
    )
    def post(self, request, pk):
        offer = get_object_or_404(RequestOffer, pk=pk, interpreter__user=request.user)
        serializer = OfferResponseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            offer = respond_to_offer(
                offer, serializer.validated_data["action"], request=request
            )
        except OfferNotActive as error:
            return Response(
                {"detail": str(error), "code": "offer_not_active"},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(
            {
                "status": OFFER_STATUS_FOR_SCREEN[offer.outcome],
                "requestId": str(offer.service_request_id),
            }
        )


class InterpreterOfferStateView(APIView):
    """GET /api/v1/interpreters/me/offers/<id>/state/: whether the offer is still waiting (V2-010)."""

    permission_classes = [IsAuthenticated, IsInterpreter]

    @extend_schema(responses={200: OfferStatusSerializer})
    def get(self, request, pk):
        offer = get_object_or_404(RequestOffer, pk=pk, interpreter__user=request.user)
        expire_stale_offers(service_request=offer.service_request)
        offer.refresh_from_db()
        return Response({"status": OFFER_STATUS_FOR_SCREEN[offer.outcome]})


# --- V2-012: hearing guests (no account) invited by the Deaf user ---


class SessionInvitationListCreateView(APIView):
    """GET lists the guests of a session. POST invites a hearing guest by email."""

    permission_classes = CAN_REQUEST

    def get(self, request, pk):
        found = get_object_or_404(ServiceRequest, pk=pk, requester=request.user)
        return Response(InvitationSerializer(found.invitations.all(), many=True).data)

    def post(self, request, pk):
        found = get_object_or_404(ServiceRequest, pk=pk, requester=request.user)
        # Rule: invites only work once the interpreter accepted, and while the session runs.
        if found.status != ServiceRequest.Status.ASSIGNED or found.ended_at is not None:
            return Response(
                {
                    "detail": "You can invite someone once an interpreter has accepted.",
                    "code": "session_not_ready",
                },
                status=status.HTTP_409_CONFLICT,
            )
        serializer = CreateInvitationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invitation = serializer.save(service_request=found)
        try:
            send_invitation_email(invitation, request.user)
        except Exception:
            # Do not leave an invitation nobody received. The Deaf user can try again.
            logger.exception("Could not send the invitation email")
            invitation.delete()
            return Response(
                {
                    "detail": "We could not send the invitation email. Please try again.",
                    "code": "email_failed",
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response(
            InvitationSerializer(invitation).data, status=status.HTTP_201_CREATED
        )


class SessionInvitationRemoveView(APIView):
    """POST /api/v1/requests/<id>/invitations/<invitation_id>/remove/"""

    permission_classes = CAN_REQUEST

    def post(self, request, pk, invitation_id):
        found = get_object_or_404(ServiceRequest, pk=pk, requester=request.user)
        invitation = get_object_or_404(found.invitations, pk=invitation_id)
        if invitation.removed_at is None:
            invitation.removed_at = timezone.now()  # the link stops working from now on
            invitation.save(update_fields=["removed_at"])
        remove_guest_from_room(
            found, invitation
        )  # and they are disconnected if they are in the call
        return Response(InvitationSerializer(invitation).data)


def _open_invitation_or_response(token):
    """Shared by the two guest views. Returns (invitation, None) or (None, error response)."""
    invitation = (
        SessionInvitation.objects.select_related("service_request__interpreter")
        .filter(token=token)
        .first()
    )
    if invitation is None:
        return None, Response(
            {"detail": "This link is not valid.", "code": "invalid_link"}, status=404
        )
    if not invitation.is_open:
        return None, Response(
            {
                "detail": "This session has ended or you were removed.",
                "code": "link_closed",
            },
            status=status.HTTP_410_GONE,
        )
    return invitation, None


class GuestLobbyView(APIView):
    """GET /api/v1/requests/guest/<token>/ : the lobby asks this every few seconds."""

    authentication_classes = []  # guests have no account, the token in the link is their key
    permission_classes = [AllowAny]

    def get(self, request, token):
        invitation, error = _open_invitation_or_response(token)
        if error:
            return error
        return Response(
            {"interpreter_here": interpreter_is_in_room(invitation.service_request)}
        )


class GuestJoinView(APIView):
    """POST /api/v1/requests/guest/<token>/join/ : the guest enters the live session."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, token):
        invitation, error = _open_invitation_or_response(token)
        if error:
            return error
        session = invitation.service_request
        if not interpreter_is_in_room(session):
            return Response(
                {
                    "detail": "The interpreter has not arrived yet.",
                    "code": "interpreter_not_here",
                },
                status=status.HTTP_409_CONFLICT,
            )
        if invitation.joined_at is None:  # first time in: the Deaf user will see this
            invitation.joined_at = timezone.now()
            invitation.save(update_fields=["joined_at"])
        return Response(
            CallTokenSerializer(
                {
                    "token": create_guest_token(session, invitation),
                    "url": settings.LIVEKIT_URL,
                    "room": room_name(session),
                }
            ).data
        )
