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
