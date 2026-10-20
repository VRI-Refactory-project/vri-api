from django.conf import settings
from django.db import models

from apps.interpreters.models import InterpreterProfile, Specialisation


class ServiceRequest(models.Model):
    class Status(models.TextChoices):
        CREATED = "CREATED", "Created"  # just made, not routed yet (only lasts during the create call)
        PENDING = "PENDING", "Pending"  # waiting for an interpreter
        OFFERED = "OFFERED", "Offered"  # sent to one interpreter, no answer yet
        ASSIGNED = "ASSIGNED", "Assigned"  # an interpreter accepted
        CANCELLED = "CANCELLED", "Cancelled"

    class CancelReason(models.TextChoices):
        REQUESTER_DISCONNECTED = "REQUESTER_DISCONNECTED", "Requester disconnected"

    class Priority(models.TextChoices):
        NORMAL = "NORMAL", "Normal"
        EMERGENCY = "EMERGENCY", "Emergency"

    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="service_requests",
    )
    # The interpreter this request is with: offered it (OFFERED) or accepted it (ASSIGNED).
    interpreter = models.ForeignKey(
        InterpreterProfile,
        on_delete=models.SET_NULL,
        related_name="assigned_service_requests",
        null=True,
        blank=True,
    )
    specialisation_required = models.CharField(
        max_length=20,
        choices=Specialisation.choices,
        default=Specialisation.GENERAL,
    )
    description = models.TextField(blank=True, default="")
    priority = models.CharField(
        max_length=10, choices=Priority.choices, default=Priority.NORMAL
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    # Sent by the app so a retry after a dropped connection does not make a second request.
    request_key = models.CharField(max_length=64, null=True, blank=True)
    offered_at = models.DateTimeField(null=True, blank=True)
    # True when a specialist was asked for and a general interpreter was offered instead.
    matched_by_fallback = models.BooleanField(default=False)
    assigned_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(
        max_length=30, choices=CancelReason.choices, blank=True, default=""
    )
    # Updated each time the requester's app asks for the status: proof they are still there.
    last_seen_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # A person can only have one open request at a time.
            models.UniqueConstraint(
                fields=["requester"],
                condition=models.Q(status__in=["CREATED", "PENDING", "OFFERED"]),
                name="one_open_request_per_requester",
            ),
            # An interpreter can only hold one offer at a time.
            models.UniqueConstraint(
                fields=["interpreter"],
                condition=models.Q(status="OFFERED"),
                name="one_offer_per_interpreter",
            ),
            # The same key from the same person is the same request.
            models.UniqueConstraint(
                fields=["requester", "request_key"],
                condition=models.Q(request_key__isnull=False),
                name="unique_request_key_per_requester",
            ),
        ]

    def __str__(self):
        return f"Request {self.pk} ({self.status})"


# A request is "open" while it is still looking for an interpreter.
OPEN_STATUSES = (
    ServiceRequest.Status.CREATED,
    ServiceRequest.Status.PENDING,
    ServiceRequest.Status.OFFERED,
)


class RequestOffer(models.Model):
    """Every time a request was offered to an interpreter, and what they did.

    This is the record of declined offers. It also tells the router who has already been asked
    about a request, and who was offered something longest ago.
    """

    class Outcome(models.TextChoices):
        PENDING = "PENDING", "Waiting for an answer"
        ACCEPTED = "ACCEPTED", "Accepted"
        DECLINED = "DECLINED", "Declined"
        EXPIRED = "EXPIRED", "Not answered in time"
        WITHDRAWN = "WITHDRAWN", "Withdrawn"

    service_request = models.ForeignKey(
        ServiceRequest, on_delete=models.CASCADE, related_name="offers"
    )
    interpreter = models.ForeignKey(
        InterpreterProfile, on_delete=models.CASCADE, related_name="request_offers"
    )
    offered_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    outcome = models.CharField(
        max_length=10, choices=Outcome.choices, default=Outcome.PENDING
    )
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["offered_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["service_request", "interpreter"],
                name="one_offer_per_request_and_interpreter",
            ),
        ]
