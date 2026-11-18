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


class Message(models.Model):
    """One message (text or a sticker) between two connected people.

    A conversation is the connection itself: there is one conversation per accepted connection, and
    removing the connection removes its messages.
    """

    MAX_LENGTH = 2000

    connection = models.ForeignKey(
        Connection, on_delete=models.CASCADE, related_name="messages"
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="community_messages_sent",
    )
    body = models.TextField(
        max_length=MAX_LENGTH,
        blank=True,
        help_text="Empty for a sticker, and for a photo or file sent without a caption.",
    )
    sticker = models.CharField(
        max_length=40,
        blank=True,
        help_text="The id of a sticker from apps.community.stickers; empty for a text message.",
    )
    has_image = models.BooleanField(
        default=False,
        help_text="A photo goes with this message (see MessageImage). Kept here so lists never load photos.",
    )
    has_file = models.BooleanField(
        default=False,
        help_text="A document goes with this message (see MessageFile). Kept here so lists never load files.",
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    read_at = models.DateTimeField(
        null=True, blank=True, help_text="When the other person opened it."
    )

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["connection", "id"])]

    def __str__(self):
        return f"{self.sender_id}: {self.body[:30] or self.sticker}"


class MessageImage(models.Model):
    """The photo of a message, kept in the database as a JPEG (not on the server's disk)."""

    message = models.OneToOneField(
        Message, on_delete=models.CASCADE, related_name="image"
    )
    data = models.BinaryField()
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()

    def __str__(self):
        return f"Photo of message {self.message_id}"


class MessageFile(models.Model):
    """The document of a message, kept in the database (not on the server's disk)."""

    message = models.OneToOneField(
        Message, on_delete=models.CASCADE, related_name="file"
    )
    name = models.CharField(max_length=100)
    content_type = models.CharField(max_length=100)
    size = models.PositiveIntegerField()
    data = models.BinaryField()

    def __str__(self):
        return f"{self.name} ({self.size} bytes)"
