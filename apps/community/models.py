from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


class Connection(models.Model):
    """One Deaf user asking another to connect, and the answer.

    Two people are connected once the addressee accepts. A declined request stays on record so the
    same person cannot keep asking.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"

    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="connections_sent",
    )
    addressee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="connections_received",
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    created_at = models.DateTimeField(default=timezone.now)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["requester", "addressee"], name="community_one_request_per_pair"
            ),
            models.CheckConstraint(
                condition=~Q(requester=F("addressee")),
                name="community_no_self_connection",
            ),
        ]

    def __str__(self):
        return f"{self.requester_id} -> {self.addressee_id} ({self.status})"
