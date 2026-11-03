from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.realtime.serializers import TicketSerializer
from apps.realtime.tickets import TICKET_MAX_AGE_SECONDS, issue_ticket


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
