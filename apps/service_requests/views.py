from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsDeafUser, IsInterpreter
from apps.service_requests.models import OPEN_STATUSES, RequestOffer, ServiceRequest
from apps.service_requests.serializers import (
    OFFER_STATUS_FOR_SCREEN,
    InterpreterOfferSerializer,
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


class ServiceRequestListCreateView(generics.ListCreateAPIView):
    """GET /api/v1/requests/ lists your requests. POST asks for an interpreter."""

    serializer_class = ServiceRequestSerializer
    permission_classes = [IsAuthenticated, IsDeafUser]

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

    permission_classes = [IsAuthenticated, IsDeafUser]

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

    permission_classes = [IsAuthenticated, IsDeafUser]

    @extend_schema(responses={200: ServiceRequestSerializer})
    def get(self, request, pk):
        found = get_object_or_404(ServiceRequest, pk=pk, requester=request.user)
        found = refresh_for_requester(found)
        return Response(ServiceRequestSerializer(found).data)


class ServiceRequestCancelView(APIView):
    """POST /api/v1/requests/<id>/cancel/"""

    permission_classes = [IsAuthenticated, IsDeafUser]

    @extend_schema(request=None, responses={200: ServiceRequestSerializer, 409: None})
    def post(self, request, pk):
        found = get_object_or_404(ServiceRequest, pk=pk, requester=request.user)
        try:
            cancelled = cancel_request(found)
        except NotCancellable:
            return Response(
                {
                    "detail": "This request can no longer be cancelled.",
                    "code": "not_cancellable",
                },
                status=status.HTTP_409_CONFLICT,
            )
        return Response(ServiceRequestSerializer(cancelled).data)


class InterpreterOffersView(APIView):
    """GET /api/v1/interpreters/me/requests/: requests currently offered to the signed-in interpreter."""

    permission_classes = [IsAuthenticated, IsInterpreter]

    @extend_schema(responses={200: OfferedRequestSerializer(many=True)})
    def get(self, request):
        expire_stale_offers(interpreter_user=request.user)
        offered = ServiceRequest.objects.filter(
            status=ServiceRequest.Status.OFFERED, interpreter__user=request.user
        ).select_related("requester")
        return Response(OfferedRequestSerializer(offered, many=True).data)


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
            409: OpenApiResponse(description="The offer expired, was withdrawn, or was already answered."),
        },
    )
    def post(self, request, pk):
        offer = get_object_or_404(RequestOffer, pk=pk, interpreter__user=request.user)
        serializer = OfferResponseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            offer = respond_to_offer(offer, serializer.validated_data["action"], request=request)
        except OfferNotActive as error:
            return Response(
                {"detail": str(error), "code": "offer_not_active"},
                status=status.HTTP_409_CONFLICT,
            )
        return Response({"status": OFFER_STATUS_FOR_SCREEN[offer.outcome]})


class InterpreterOfferStateView(APIView):
    """GET /api/v1/interpreters/me/offers/<id>/state/: whether the offer is still waiting (V2-010)."""

    permission_classes = [IsAuthenticated, IsInterpreter]

    @extend_schema(responses={200: OfferStatusSerializer})
    def get(self, request, pk):
        offer = get_object_or_404(RequestOffer, pk=pk, interpreter__user=request.user)
        expire_stale_offers(service_request=offer.service_request)
        offer.refresh_from_db()
        return Response({"status": OFFER_STATUS_FOR_SCREEN[offer.outcome]})
