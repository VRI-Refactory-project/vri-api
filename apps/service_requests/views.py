from django.conf import settings
from django.shortcuts import get_object_or_404
from django.utils.crypto import constant_time_compare
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import generics, serializers, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsDeafUser, IsInterpreter
from apps.service_requests.models import OPEN_STATUSES, ServiceRequest
from apps.service_requests.serializers import (
    OfferedRequestSerializer,
    ServiceRequestSerializer,
)
from apps.service_requests.services import (
    NotCancellable,
    OpenRequestExists,
    cancel_request,
    create_request,
    refresh_for_requester,
    sweep_requests,
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
        if open_request is not None:
            refresh_for_requester(open_request)
        if open_request is None or open_request.status not in OPEN_STATUSES:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(ServiceRequestSerializer(open_request).data)


class ServiceRequestDetailView(APIView):
    """GET /api/v1/requests/<id>/: where the request is now. Asking also counts as "still here"."""

    permission_classes = [IsAuthenticated, IsDeafUser]

    @extend_schema(responses={200: ServiceRequestSerializer})
    def get(self, request, pk):
        found = get_object_or_404(ServiceRequest, pk=pk, requester=request.user)
        refresh_for_requester(found)
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
        offered = ServiceRequest.objects.filter(
            status=ServiceRequest.Status.OFFERED, interpreter__user=request.user
        ).select_related("requester")
        return Response(OfferedRequestSerializer(offered, many=True).data)


class RequestSweepView(APIView):
    """POST /api/v1/requests/sweep/: apply request timeouts to every open request (V2-009).

    There is no background worker on the free hosting plan, so the uptime monitor calls this with the
    X-Sweep-Token header. Safe to call repeatedly.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(
        request=None,
        responses={
            200: inline_serializer(
                "RequestSweepResult",
                {"cancelled_requester_disconnected": serializers.IntegerField()},
            ),
            403: OpenApiResponse(description="Missing or wrong X-Sweep-Token."),
            404: OpenApiResponse(description="The sweep is turned off (REQUEST_SWEEP_TOKEN not set)."),
        },
    )
    def post(self, request):
        expected = settings.REQUEST_SWEEP_TOKEN
        if not expected:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if not constant_time_compare(request.headers.get("X-Sweep-Token", ""), expected):
            return Response({"detail": "Invalid sweep token."}, status=status.HTTP_403_FORBIDDEN)
        return Response(sweep_requests())
