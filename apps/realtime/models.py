from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.interpreters.models import InterpreterProfile
from apps.service_requests.models import ServiceRequest


class VideoSession(models.Model):
    """Tracks the actual LiveKit call lifecycle separately from request routing."""

    class Status(models.TextChoices):
        WAITING = "waiting", "Waiting"
        CONNECTING = "connecting", "Connecting"
        CONNECTED = "connected", "Connected"
        ENDED = "ended", "Ended"
        FAILED = "failed", "Failed"  # the call never connected in time (RT-03)

    service_request = models.OneToOneField(
        ServiceRequest,
        on_delete=models.CASCADE,
        related_name="video_session",
    )
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.WAITING,
        db_index=True,
    )
    joined_participants = models.JSONField(default=list, blank=True)
    connected_at = models.DateTimeField(null=True, blank=True)
    requester_joined_at = models.DateTimeField(null=True, blank=True)
    interpreter_joined_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Video session for request {self.service_request_id} ({self.status})"


class SessionFeedback(models.Model):
    """Feedback on a completed session, one per person who took part."""

    video_session = models.ForeignKey(
        VideoSession,
        on_delete=models.CASCADE,
        related_name="feedback",
    )
    interpreter = models.ForeignKey(
        InterpreterProfile,
        on_delete=models.PROTECT,
        related_name="session_feedback",
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="submitted_session_feedback",
    )
    rating = models.PositiveSmallIntegerField(
        validators=[
            MinValueValidator(1),
            MaxValueValidator(5),
        ],
    )

    class Category(models.TextChoices):
        AUDIO = "Audio", "Audio"
        VIDEO = "Video", "Video"
        OTHER = "Other", "Other"

    # What went wrong, as picked in the feedback wizard (all optional).
    category = models.CharField(
        max_length=10, choices=Category.choices, blank=True, default=""
    )
    issues = models.JSONField(default=list, blank=True)
    comment = models.TextField(blank=True, default="")
    accuracy_comment = models.TextField(blank=True, default="")
    clarity_comment = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["video_session", "submitted_by"],
                name="one_feedback_per_person_and_video_session",
            ),
        ]

    def __str__(self):
        return f"Feedback for video session {self.video_session_id}: {self.rating}/5"


class CallDeparture(models.Model):
    """A participant who has left a call and may still come back (V2-033).

    It stays open until the person rejoins, or until the grace window has passed and the session has
    been settled. Only the identity (user-<id> or guest-<id>) and the reason are stored, never content.
    """

    class Reason(models.TextChoices):
        LEFT = "left", "Left on purpose"
        DROPPED = "dropped", "Connection dropped"

    video_session = models.ForeignKey(
        VideoSession,
        on_delete=models.CASCADE,
        related_name="departures",
    )
    identity = models.CharField(max_length=64)
    reason = models.CharField(max_length=10, choices=Reason.choices)
    left_at = models.DateTimeField()
    # Set when the person rejoins, or when the grace window ends (see apps/realtime/departures.py).
    settled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["left_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["video_session", "identity"],
                condition=models.Q(settled_at__isnull=True),
                name="one_open_departure_per_person",
            ),
        ]
        indexes = [models.Index(fields=["settled_at", "left_at"], name="departure_open_idx")]

    def __str__(self):
        return f"{self.identity} {self.get_reason_display()} from session {self.video_session_id}"
