from django.conf import settings
from django.db import models


class UserProfile(models.Model):
    # Link directly to the Django User model
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )

    # Personal & Contact Information
    contact_number = models.CharField(max_length=20, unique=True, null=True, blank=True)
    preferred_name = models.CharField(max_length=100, blank=True)
    location = models.CharField(max_length=255, blank=True, help_text="e.g. Kampala")

    # Emergency Contact Details (V2-001 / Figma UI)
    emergency_contact_name = models.CharField(
        max_length=255,
        blank=True,
        help_text="e.g. Sarah Kiggundu (Sister)",
    )
    emergency_contact_phone = models.CharField(
        max_length=20, blank=True, help_text="e.g. +256 700 987 654"
    )

    # Sign Language & Communication Preferences
    sign_language_preference = models.CharField(
        max_length=100, default="Ugandan Sign Language"
    )
    communication_methods = models.JSONField(
        default=list,
        blank=True,
        help_text="e.g. ['Ugandan Sign Language', 'English (text)', 'International Sign']",
    )

    # Sensory Accessibility Toggles (Figma Dashboard)
    flash_alerts = models.BooleanField(
        default=True, help_text="Flash alert for incoming calls"
    )
    vibrate_on_call = models.BooleanField(
        default=True, help_text="Vibrate on incoming call"
    )
    live_captions = models.BooleanField(
        default=False, help_text="Enable live automated/manual captions"
    )
    large_text = models.BooleanField(default=False, help_text="Increase UI font size")

    # Interpreter Domain Preferences
    preferred_domains = models.JSONField(
        default=list,
        blank=True,
        help_text="e.g. ['healthcare', 'legal', 'education']",
    )
    preferred_contact_mode = models.CharField(max_length=50, default="Video, Chat")

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.email} ({self.preferred_name or self.user.get_full_name() or 'Deaf User'})"
