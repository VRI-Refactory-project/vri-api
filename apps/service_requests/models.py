from django.conf import settings
from django.db import models
from apps.interpreters.models import Specialisation


class SessionRequestStatus(models.TextChoices):
    CREATED = "CREATED", "Created"
    QUEUED = "QUEUED", "Waiting for an interpreter"
    OFFERED = "OFFERED", "Offered to interpreter"
    ACCEPTED = "ACCEPTED", "Accepted"
    DECLINED = "DECLINED", "Declined"
    EXPIRED = "EXPIRED", "Expired"
    CONNECTING = "CONNECTING", "Connecting"
    ACTIVE = "ACTIVE", "Connected"
    COMPLETED = "COMPLETED", "Session ended"
    CANCELLED = "CANCELLED", "Request cancelled"
    FAILED = "FAILED", "No interpreter available"


class SessionRequest(models.Model):
    # --- Participants ---
    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="session_requests",
    )
    requester_role = models.CharField(
        max_length=20,
        default="deaf_hoh",
        help_text="Role at time of request (deaf_hoh or institution) for audit trail.",
    )
    institution = models.ForeignKey(
        "institutions.InstitutionProfile",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="institutional_requests",
        help_text="Attributed institution when requested by institutional staff.",
    )
    offered_interpreter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="offered_requests",
        help_text="Interpreter currently holding the routed offer.",
    )
    accepted_interpreter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="accepted_requests",
        help_text="Interpreter who accepted and handled the call.",
    )

    # --- Request Details ---
    specialisation = models.CharField(
        max_length=20,
        choices=Specialisation.choices,
        default=Specialisation.GENERAL,
        help_text="Required domain skill (medical, legal, general).",
    )
    description = models.TextField(
        blank=True,
        default="",
        help_text="Optional requester notes or context description.",
    )
    is_emergency = models.BooleanField(
        default=False,
        db_index=True,
        help_text="V2-024 emergency priority flag.",
    )
    status = models.CharField(
        max_length=20,
        choices=SessionRequestStatus.choices,
        default=SessionRequestStatus.CREATED,
        db_index=True,
    )

    # --- Routing & Idempotency Metadata ---
    request_key = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        help_text="Client-generated key to prevent duplicate bookings on retry.",
    )
    matched_by_fallback = models.BooleanField(
        default=False,
        help_text="True if matched to a general interpreter due to specialist unavailability.",
    )

    # --- Lifecycle Timestamps ---
    created_at = models.DateTimeField(auto_now_add=True)
    queued_at = models.DateTimeField(null=True, blank=True)
    offered_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # Emergency calls jump ahead in database queries; ties are broken by FIFO created_at ordering
        ordering = ["-is_emergency", "created_at"]
        indexes = [
            models.Index(fields=["status", "is_emergency", "created_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["requester", "request_key"],
                name="unique_requester_request_key",
                condition=models.Q(request_key__isnull=False),
            )
        ]

    def __str__(self):
        return f"Request #{self.id} ({self.status}) - Emergency: {self.is_emergency}"

    @property
    def is_queued(self) -> bool:
        return self.status == SessionRequestStatus.QUEUED

    @property
    def is_terminal(self) -> bool:
        return self.status in [
            SessionRequestStatus.COMPLETED,
            SessionRequestStatus.CANCELLED,
            SessionRequestStatus.FAILED,
        ]
