from django.conf import settings
from django.db import models
from django.utils import timezone


class WalletEntry(models.Model):
    """One line in a person's wallet ledger. Entries are never edited or deleted, only settled.

    The amount is in whole Ugandan shillings: positive money in, negative money out. A person's
    balance is worked out from their entries (see services.balance), never stored.
    """

    class Kind(models.TextChoices):
        TOPUP = "topup", "Top-up"  # money paid in by mobile money
        PAYOUT = "payout", "Payout"  # money paid out to the person's mobile money
        SESSION_CHARGE = (
            "session_charge",
            "Session charge",
        )  # reserved for paying for calls
        SESSION_EARNING = (
            "session_earning",
            "Session earning",
        )  # reserved for paying interpreters
        ADJUSTMENT = "adjustment", "Adjustment"  # a correction made by an administrator

    class Status(models.TextChoices):
        PENDING = (
            "pending",
            "Pending",
        )  # waiting for the customer, or for an administrator
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"  # never happened; it does not count in the balance

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,  # a ledger must not vanish with the account
        related_name="wallet_entries",
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    amount = models.BigIntegerField(help_text="Whole UGX. Positive in, negative out.")
    currency = models.CharField(max_length=3, default="UGX")
    # Ours: sent to the provider as its transaction reference and used to match its replies.
    reference = models.CharField(max_length=64, unique=True)
    provider = models.CharField(max_length=20, blank=True)
    provider_ref = models.CharField(max_length=64, blank=True)
    phone_number = models.CharField(max_length=20, blank=True)
    network = models.CharField(max_length=10, blank=True)
    description = models.CharField(max_length=200, blank=True)
    failure_reason = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name_plural = "wallet entries"
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(amount=0), name="wallet_entry_amount_not_zero"
            ),
        ]

    def __str__(self):
        return f"{self.user_id} {self.kind} {self.amount} ({self.status})"
