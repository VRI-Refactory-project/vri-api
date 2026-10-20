from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from .models import SessionRequest
from .serializers import (
    SessionRequestCreateSerializer,
    SessionRequestSerializer,
    SessionStatusSerializer,
)
from .services import create_session_request, expire_stale_queued_requests


@extend_schema(tags=["Session requests"])
class SessionRequestListCreateView(generics.ListCreateAPIView):
    """V2-022 / V2-023: List all session requests for the authenticated user, or create a new request.

    When created, the request is immediately routed to a free interpreter or placed
    in the QUEUED state.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        if self.request.method == "POST":
            return SessionRequestCreateSerializer
        return SessionRequestSerializer

    def get_queryset(self):
        # Requesters can only access their own session requests
        return (
            SessionRequest.objects.filter(requester=self.request.user)
            .select_related("requester", "offered_interpreter", "accepted_interpreter")
            .order_by("-created_at")
        )

    @extend_schema(
        summary="List my session requests",
        description="Returns a list of all historical and active requests submitted by the current user.",
        responses={200: SessionRequestSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        expire_stale_queued_requests()
        return super().get(request, *args, **kwargs)

    @extend_schema(
        summary="Request an interpreter",
        description=(
            "V2-022 / V2-023: Creates a request and offers it to an available interpreter. "
            "If no interpreter is available, it is automatically placed in QUEUED state (never rejected)."
        ),
        request=SessionRequestCreateSerializer,
        responses={201: SessionRequestSerializer},
    )
    def create(self, request, *args, **kwargs):
        # Block creation attempts from interpreters (returns 403 Forbidden)
        if getattr(request.user, "role", None) == "interpreter":
            raise PermissionDenied("Interpreters cannot create session requests.")

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Unified creation & routing service call
        session_request = create_session_request(
            requester=request.user,
            specialisation=serializer.validated_data.get("specialisation", "general"),
            description=serializer.validated_data.get("description", ""),
            is_emergency=serializer.validated_data.get("is_emergency", False),
            request_key=serializer.validated_data.get("request_key"),
        )

        response_serializer = SessionRequestSerializer(
            session_request, context={"request": request}
        )
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)


@extend_schema(tags=["Session requests"])
class SessionRequestDetailView(generics.RetrieveAPIView):
    """V2-023: Retrieve full details and status for a specific session request.

    Automatically checks and expires timed-out queued requests before returning.
    """

    serializer_class = SessionRequestSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return SessionRequest.objects.filter(
            requester=self.request.user
        ).select_related("requester", "offered_interpreter", "accepted_interpreter")

    @extend_schema(
        summary="Get request detail",
        description="Returns full session request details, including current status and 1-based queue position.",
        responses={200: SessionRequestSerializer},
    )
    def get(self, request, *args, **kwargs):
        expire_stale_queued_requests()
        return super().get(request, *args, **kwargs)


@extend_schema(tags=["Session requests"])
class SessionStatusView(generics.RetrieveAPIView):
    """V2-023-MOB-02: Lightweight status polling endpoint for mobile clients to monitor queue position.

    Minimizes payload size over low-bandwidth network connections.
    """

    serializer_class = SessionStatusSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return SessionRequest.objects.filter(requester=self.request.user)

    @extend_schema(
        summary="Poll request status",
        description="Lightweight status check for polling during queue waiting state.",
        responses={200: SessionStatusSerializer},
    )
    def get(self, request, *args, **kwargs):
        expire_stale_queued_requests()
        return super().get(request, *args, **kwargs)
