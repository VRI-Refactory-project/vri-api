from django.conf import settings
from django.db import models


class SupportRequest(models.Model):
    """A private support ticket submitted by a signed-in platform user (V2-071)."""

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        IN_PROGRESS = "IN_PROGRESS", "In progress"
        RESOLVED = "RESOLVED", "Resolved"

    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="support_requests",
    )
    requester_name = models.CharField(max_length=150)
    requester_role = models.CharField(max_length=20)
    requester_institution_name = models.CharField(max_length=200, blank=True, default="")
    requester_institution_role = models.CharField(max_length=10, blank=True, default="")
    subject = models.CharField(max_length=200)
    category = models.CharField(max_length=50)
    message = models.TextField(max_length=5000)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True)
    admin_response = models.TextField(max_length=5000, blank=True, default="")
    follow_up = models.TextField(max_length=5000, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"Support request #{self.pk}: {self.subject}"


class AdminProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="admin_profile",
    )
    session_alerts = models.BooleanField(
        default=True,
        help_text="Receive alerts for connection failures & sessions needing attention.",
    )
    verification_alerts = models.BooleanField(
        default=True,
        help_text="Receive alerts for new interpreter & institution applications.",
    )
    password_last_changed = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp recording when the administrator last updated their password.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"AdminProfile ({self.user.email})"
