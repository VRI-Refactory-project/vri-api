from django.db import models

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
