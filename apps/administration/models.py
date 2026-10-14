from django.conf import settings
from django.db import models


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
