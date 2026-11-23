import logging

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import APIException, NotFound, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsDeafUser, IsInterpreter

from . import services
from .models import WalletEntry
from .providers import ProviderError, ProviderNotConfigured, get_provider
from .serializers import (
    EntrySerializer,
    PayoutRequestSerializer,
    TopUpRequestSerializer,
    WalletSerializer,
    entry_data,
)

logger = logging.getLogger(__name__)
TAGS = ["Wallet"]


class PaymentsUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Payments are not available yet."
    default_code = "payments_unavailable"


class ProviderUnavailable(APIException):
    status_code = status.HTTP_502_BAD_GATEWAY
    default_detail = "The payment provider could not be reached. Try again in a moment."
    default_code = "provider_unavailable"


class WalletView(APIView):
    permission_classes = [IsDeafUser | IsInterpreter]

    @extend_schema(
        tags=TAGS,
        summary="My wallet: balance and recent entries",
        responses={200: WalletSerializer},
    )
    def get(self, request):
        return Response(
            {
                "currency": "UGX",
                "payments_available": get_provider().is_configured,
                **services.balance(request.user),
                "transactions": [
                    entry_data(e) for e in services.recent_entries(request.user)
                ],
            }
        )


class TopUpCreateView(APIView):
    permission_classes = [IsDeafUser]

    @extend_schema(
        tags=TAGS,
        summary="Top up my wallet by mobile money",
        description=(
            "Sends an approval request to the phone. The entry stays `pending` until the person "
            "approves it; poll `GET topups/<reference>/`."
        ),
        request=TopUpRequestSerializer,
        responses={201: EntrySerializer},
    )
    def post(self, request):
        serializer = TopUpRequestSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            entry = services.start_topup(
                request.user, data["amount"], data["phone_number"], data["network"]
            )
        except ProviderNotConfigured:
            raise PaymentsUnavailable() from None
        except ProviderError:
            raise ProviderUnavailable() from None
        return Response(entry_data(entry), status=status.HTTP_201_CREATED)


class TopUpDetailView(APIView):
    permission_classes = [IsDeafUser]

    @extend_schema(
        tags=TAGS,
        summary="Check a top-up",
        description="Asks the provider about a waiting top-up, so it settles even if its webhook is late.",
        responses={200: EntrySerializer},
    )
    def get(self, request, reference):
        entry = WalletEntry.objects.filter(
            user=request.user, reference=reference, kind=WalletEntry.Kind.TOPUP
        ).first()
        if entry is None:
            raise NotFound()
        return Response(entry_data(services.refresh_topup(entry)))


class PayoutCreateView(APIView):
    permission_classes = [IsInterpreter]

    @extend_schema(
        tags=TAGS,
        summary="Ask for my earnings to be paid out",
        description="The money is held straight away and an administrator sends it.",
        request=PayoutRequestSerializer,
        responses={201: EntrySerializer},
    )
    def post(self, request):
        serializer = PayoutRequestSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            entry = services.request_payout(
                request.user, data["amount"], data["phone_number"], data["network"]
            )
        except services.InsufficientFunds:
            raise ValidationError(
                {"amount": ["You do not have enough in your wallet."]}
            ) from None
        return Response(entry_data(entry), status=status.HTTP_201_CREATED)


class FlutterwaveWebhookView(APIView):
    """Flutterwave tells us a payment changed. We only believe it after asking Flutterwave ourselves."""

    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(exclude=True)
    def post(self, request):
        provider = get_provider()
        if not provider.webhook_is_genuine(request.headers.get("verif-hash")):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        data = request.data.get("data") if isinstance(request.data, dict) else None
        reference = data.get("tx_ref") if isinstance(data, dict) else None
        if not reference:
            return Response(status=status.HTTP_200_OK)
        if not WalletEntry.objects.filter(reference=reference).exists():
            return Response(
                status=status.HTTP_200_OK
            )  # not ours; stop it being retried
        try:
            services.settle_topup(reference, provider.verify(reference))
        except ProviderError:
            logger.warning(
                "Could not verify wallet payment %s; it will be retried.", reference
            )
            return Response(
                status=status.HTTP_502_BAD_GATEWAY
            )  # so Flutterwave retries
        return Response(status=status.HTTP_200_OK)
