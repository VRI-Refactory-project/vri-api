"""The wallet's rules: balances, top-ups and payouts.

Money is whole Ugandan shillings. Entries are never edited except to settle them (pending ->
completed or failed), and a settled entry never changes again, so a webhook that arrives twice, or
a customer who refreshes the page, cannot pay anyone twice.
"""

import logging
import uuid

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.notifications.services import create_notification

from .models import WalletEntry
from .providers import ProviderError, get_provider

logger = logging.getLogger(__name__)
User = get_user_model()

RECENT_ENTRIES = 50


class InsufficientFunds(Exception):
    pass


def balance(user):
    """What the person can spend now, and what is still on its way in.

    Pending money out (a payout waiting for an administrator) is already held back, so it cannot be
    spent twice. Failed entries never count.
    """
    totals = user.wallet_entries.aggregate(
        settled=Sum("amount", filter=Q(status=WalletEntry.Status.COMPLETED)),
        held=Sum("amount", filter=Q(status=WalletEntry.Status.PENDING, amount__lt=0)),
        incoming=Sum(
            "amount", filter=Q(status=WalletEntry.Status.PENDING, amount__gt=0)
        ),
        earned=Sum(
            "amount", filter=Q(status=WalletEntry.Status.COMPLETED, amount__gt=0)
        ),
    )
    return {
        "available": (totals["settled"] or 0) + (totals["held"] or 0),
        "pending": totals["incoming"] or 0,
        # Everything that has ever come in, paid out or not (the interpreter's "total earnings").
        "earned": totals["earned"] or 0,
    }


def recent_entries(user):
    return list(user.wallet_entries.all()[:RECENT_ENTRIES])


def _new_reference(prefix):
    return f"{prefix}-{uuid.uuid4().hex}"


def _finish(entry, status, reason=""):
    entry.status = status
    entry.failure_reason = reason[:200]
    entry.completed_at = timezone.now()
    entry.save(
        update_fields=["status", "failure_reason", "completed_at", "provider_ref"]
    )


# --- top-ups ---


def start_topup(user, amount, phone, network):
    """Ask the person's phone to approve a payment. The money arrives later (see settle_topup).

    Raises ProviderNotConfigured when payments are off (nothing is saved) and ProviderError when the
    provider cannot be reached (the attempt is saved as failed).
    """
    provider = get_provider()
    provider.ensure_ready()
    entry = WalletEntry.objects.create(
        user=user,
        kind=WalletEntry.Kind.TOPUP,
        amount=amount,
        reference=_new_reference("topup"),
        provider=provider.name,
        phone_number=phone,
        network=network,
        description=f"Top-up from {network.title()} Mobile Money",
    )
    try:
        charge = provider.charge_mobile_money(
            entry.reference, amount, phone, network, user.email, user.full_name
        )
    except ProviderError:
        _finish(
            entry, WalletEntry.Status.FAILED, "Could not reach the payment provider."
        )
        raise
    if charge.status == "failed":
        _finish(
            entry,
            WalletEntry.Status.FAILED,
            charge.message or "The payment was refused.",
        )
    else:
        entry.provider_ref = charge.provider_ref
        entry.save(update_fields=["provider_ref"])
    return entry


def settle_topup(reference, verification):
    """Apply what the provider says happened. Safe to call any number of times."""
    with transaction.atomic():
        entry = (
            WalletEntry.objects.select_for_update()
            .filter(reference=reference, kind=WalletEntry.Kind.TOPUP)
            .first()
        )
        if entry is None or entry.status != WalletEntry.Status.PENDING:
            return entry
        if verification.status == "pending":
            return entry
        if verification.status == "failed":
            _finish(entry, WalletEntry.Status.FAILED, "The payment was not approved.")
            return entry
        if (
            verification.currency != entry.currency
            or verification.amount < entry.amount
        ):
            logger.error(
                "Wallet top-up %s paid %s %s but %s UGX was expected; not credited.",
                reference,
                verification.amount,
                verification.currency,
                entry.amount,
            )
            _finish(
                entry,
                WalletEntry.Status.FAILED,
                "The amount paid did not match. An administrator will look into it.",
            )
            return entry
        entry.provider_ref = verification.provider_ref or entry.provider_ref
        _finish(entry, WalletEntry.Status.COMPLETED)
        create_notification(
            user=entry.user,
            title="Top-up received",
            body=f"UGX {entry.amount:,} was added to your wallet.",
            data={"url": "/dashboard/deaf/wallet"},
        )
        return entry


def refresh_topup(entry):
    """Check a waiting top-up with the provider. Used when the person's app asks about it, so it still
    settles if the provider's webhook cannot reach us."""
    if entry.status != WalletEntry.Status.PENDING:
        return entry
    try:
        verification = get_provider().verify(entry.reference)
    except ProviderError:
        return entry  # try again next time
    return settle_topup(entry.reference, verification) or entry


# --- payouts ---


def request_payout(user, amount, phone, network):
    """Hold back the money and ask an administrator to send it. Raises InsufficientFunds."""
    with transaction.atomic():
        # Lock the person's row so two requests at once cannot both spend the same money.
        User.objects.select_for_update().get(pk=user.pk)
        if balance(user)["available"] < amount:
            raise InsufficientFunds
        return WalletEntry.objects.create(
            user=user,
            kind=WalletEntry.Kind.PAYOUT,
            amount=-amount,
            reference=_new_reference("payout"),
            phone_number=phone,
            network=network,
            description=f"Payout to {network.title()} Mobile Money",
        )


def _settle_payout(entry_id, status, reason="", provider_ref=""):
    with transaction.atomic():
        entry = (
            WalletEntry.objects.select_for_update()
            .filter(pk=entry_id, kind=WalletEntry.Kind.PAYOUT)
            .first()
        )
        if entry is None or entry.status != WalletEntry.Status.PENDING:
            return None
        entry.provider_ref = provider_ref
        _finish(entry, status, reason)
        paid = status == WalletEntry.Status.COMPLETED
        create_notification(
            user=entry.user,
            title="Payout sent" if paid else "Payout not sent",
            body=(
                f"UGX {-entry.amount:,} was sent to your mobile money."
                if paid
                else f"Your payout of UGX {-entry.amount:,} was not sent and the money is back in your wallet."
            ),
            data={"url": "/dashboard/interpreter/wallet"},
        )
        return entry


def complete_payout(entry_id, provider_ref=""):
    """An administrator has sent the money."""
    return _settle_payout(
        entry_id, WalletEntry.Status.COMPLETED, provider_ref=provider_ref
    )


def reject_payout(entry_id, reason="Not approved"):
    """The money is released back to the person's balance."""
    return _settle_payout(entry_id, WalletEntry.Status.FAILED, reason)
