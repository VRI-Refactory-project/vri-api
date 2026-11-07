import uuid

from django.conf import settings
from django.db import models


class VideoSession(models.Model):
    """Tracks the video-call lifecycle separately from ServiceRequest."""

    class Status(models.TextChoices):
        WAITING = "waiting", "Waiting"
        CONNECTING = "connecting", "Connecting"
        CONNECTED = "connected", "Connected"
        ENDED = "ended", "Ended"

    service_request = models.OneToOneField(
        "service_requests.ServiceRequest",
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
    ended_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Video session for request {self.service_request_id} ({self.status})"


class GuestInvitation(models.Model):
    """Secure invitation for an unauthenticated hearing participant."""

    video_session = models.ForeignKey(
        VideoSession,
        on_delete=models.CASCADE,
        related_name="guest_invitations",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="created_guest_invitations",
    )
    token_hash = models.CharField(max_length=64, unique=True)
    guest_identity = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )
    guest_name = models.CharField(max_length=100, blank=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Guest invitation for video session {self.video_session_id}"

