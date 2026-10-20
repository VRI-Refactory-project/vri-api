from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
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
)


class ServiceRequestListCreateView(generics.ListCreateAPIView):
    """GET /api/v1/requests/ lists your requests. POST asks for an interpreter."""

    serializer_class = ServiceRequestSerializer
    permission_classes = [IsAuthenticated, IsDeafUser]

    def get_queryset(self):
        return self.request.user.service_requests.select_related(
            "requester", "interpreter", "interpreter__user"
        ).all()

    @extend_schema(responses={201: ServiceRequestSerializer, 409: None})
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            service_request, created = create_request(
                requester=request.user, **serializer.validated_data
            )
        except OpenRequestExists:
            return Response(
                {
                    "detail": "You already have an open request.",
                    "code": "open_request_exists",
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
        return Response(ServiceRequestSerializer(open_request).data)


class ServiceRequestDetailView(APIView):
    """GET /api/v1/requests/<id>/: where the request is now. Asking also counts as "still here"."""

    permission_classes = [IsAuthenticated, IsDeafUser]

    @extend_schema(responses={200: ServiceRequestSerializer})
    def get(self, request, pk):
        found = get_object_or_404(ServiceRequest, pk=pk, requester=request.user)
        if found.status in OPEN_STATUSES:
            found.last_seen_at = timezone.now()
            found.save(update_fields=["last_seen_at"])
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
