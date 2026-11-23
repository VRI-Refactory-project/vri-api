"""The payment provider behind the wallet. Only Flutterwave (Uganda mobile money) for now.

Everything that talks to Flutterwave lives here, so changing provider means changing this one file.
Keys come from the environment (settings.FLUTTERWAVE_*), never from the code.
"""

import hmac
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings

TIMEOUT_SECONDS = 20


class ProviderError(Exception):
    """The provider could not be reached, or answered with something we cannot use."""


class ProviderNotConfigured(ProviderError):
    """No keys are set, so payments are switched off."""


@dataclass
class Charge:
    status: str  # "pending" (waiting for the customer to approve) or "failed"
    provider_ref: str = ""
    message: str = ""


@dataclass
class Verification:
    status: str  # "successful", "pending" or "failed"
    amount: Decimal = Decimal(0)
    currency: str = ""
    provider_ref: str = ""


class FlutterwaveProvider:
    name = "flutterwave"

    def __init__(self, secret_key=None, base_url=None):
        self.secret_key = (
            settings.FLUTTERWAVE_SECRET_KEY if secret_key is None else secret_key
        )
        self.base_url = (base_url or settings.FLUTTERWAVE_BASE_URL).rstrip("/")

    # --- helpers ---

    @property
    def is_configured(self):
        return bool(self.secret_key)

    def ensure_ready(self):
        if not self.is_configured:
            raise ProviderNotConfigured("Payments are not switched on yet.")

    def _call(self, method, path, **kwargs):
        self.ensure_ready()
        try:
            response = requests.request(
                method,
                f"{self.base_url}{path}",
                headers={"Authorization": f"Bearer {self.secret_key}"},
                timeout=TIMEOUT_SECONDS,
                **kwargs,
            )
            body = response.json()
        except (requests.RequestException, ValueError) as error:
            raise ProviderError(
                f"Could not reach the payment provider: {error}"
            ) from None
        if not isinstance(body, dict):
            raise ProviderError("The payment provider sent an unexpected reply.")
        return response, body

    # --- money in ---

    def charge_mobile_money(self, reference, amount, phone, network, email, full_name):
        """Ask the customer's phone to approve a payment. `phone` is digits only: 2567…"""
        response, body = self._call(
            "POST",
            "/charges",
            params={"type": "mobile_money_uganda"},
            json={
                "tx_ref": reference,
                "amount": amount,
                "currency": "UGX",
                "email": email,
                "phone_number": phone,
                "network": network,
                "fullname": full_name,
            },
        )
        if response.status_code >= 400 or body.get("status") != "success":
            return Charge(
                "failed", message=str(body.get("message") or "The payment was refused.")
            )
        data = body.get("data") or {}
        return Charge(
            "pending", provider_ref=str(data.get("id") or data.get("flw_ref") or "")
        )

    def verify(self, reference):
        """Ask Flutterwave what really happened to a payment. Never trust a webhook on its own."""
        response, body = self._call(
            "GET", "/transactions/verify_by_reference", params={"tx_ref": reference}
        )
        data = body.get("data")
        if response.status_code >= 400 or body.get("status") != "success" or not data:
            # Not found yet usually means the customer has not approved it.
            return Verification("pending")
        status = str(data.get("status", "")).lower()
        try:
            amount = Decimal(str(data.get("amount", "0")))
        except InvalidOperation:
            raise ProviderError(
                "The payment provider sent an unusable amount."
            ) from None
        return Verification(
            status=status if status in ("successful", "failed") else "pending",
            amount=amount,
            currency=str(data.get("currency", "")),
            provider_ref=str(data.get("id", "")),
        )

    # --- webhooks ---

    def webhook_is_genuine(self, header_value):
        """Flutterwave puts the secret hash we chose in the `verif-hash` header."""
        expected = settings.FLUTTERWAVE_WEBHOOK_HASH
        if not expected or not header_value:
            return False  # no hash set means no webhook is ever trusted
        return hmac.compare_digest(str(header_value), str(expected))


def get_provider():
    return FlutterwaveProvider()
