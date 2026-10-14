
from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    """Record administrator actions that the platform must be able to account for."""

    class Event(models.TextChoices):
        ADMIN_SIGN_IN = "admin_sign_in", "Administrator signed in"
        INSTITUTION_APPROVED = "institution_approved", "Institution approved"
        INSTITUTION_REJECTED = "institution_rejected", "Institution rejected"
        INSTITUTION_DEACTIVATED = "institution_deactivated", "Institution deactivated"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
    )
    event = models.CharField(max_length=40, choices=Event.choices, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.get_event_display()} - {self.user} - {self.created_at:%Y-%m-%d %H:%M}"
