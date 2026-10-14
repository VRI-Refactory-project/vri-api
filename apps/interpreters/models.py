from django.conf import settings
from django.core.validators import FileExtensionValidator, MaxValueValidator
from django.db import models


class VerificationStatus(models.TextChoices):
    INACTIVE = "INACTIVE", "Inactive / Pending Review"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"


class Specialisation(models.TextChoices):
    GENERAL = "GENERAL", "General"
    MEDICAL = "MEDICAL", "Medical"
    LEGAL = "LEGAL", "Legal"
    OTHER = "OTHER", "Other"


def certification_upload_path(instance, filename):
    return f"certifications/user_{instance.interpreter.user_id}/{filename}"


class InterpreterProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="interpreter_profile",
    )
    verification_status = models.CharField(
        max_length=20,
        choices=VerificationStatus.choices,
        default=VerificationStatus.INACTIVE,
        db_index=True,
    )
    is_available = models.BooleanField(default=False)
    specialisation = models.CharField(
        max_length=20,
        choices=Specialisation.choices,
        default=Specialisation.GENERAL,
    )
    specialisation_other = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="Custom specialisation text when 'OTHER' is selected.",
    )
    sign_languages = models.JSONField(
        default=list,
        blank=True,
        help_text="List of sign language preferences, e.g. ['Ugandan Sign Language']",
    )
    working_hours_schedule = models.JSONField(
        default=dict,
        blank=True,
        help_text="General weekly schedule overview JSON.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(is_available=False)
                | models.Q(verification_status=VerificationStatus.APPROVED),
                name="available_only_if_approved",
            ),
        ]

    def __str__(self):
        return f"{self.user.full_name} ({self.get_verification_status_display()})"


class WeeklyAvailability(models.Model):
    """The default week: one row per weekday for each interpreter."""

    interpreter = models.ForeignKey(
        InterpreterProfile,
        on_delete=models.CASCADE,
        related_name="weekly_availability",
    )
    weekday = models.PositiveSmallIntegerField(
        validators=[MaxValueValidator(6)],  # 0 = Monday ... 6 = Sunday
    )
    is_available = models.BooleanField(default=False)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)

    class Meta:
        ordering = ["weekday"]
        constraints = [
            models.UniqueConstraint(
                fields=["interpreter", "weekday"],
                name="one_default_row_per_weekday",
            ),
        ]

    def __str__(self):
        return f"{self.interpreter.user.full_name} - weekday {self.weekday}"


class DateException(models.Model):
    """One specific date that differs from the default week."""

    interpreter = models.ForeignKey(
        InterpreterProfile,
        on_delete=models.CASCADE,
        related_name="date_exceptions",
    )
    date = models.DateField()
    is_available = models.BooleanField(default=False)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)

    class Meta:
        ordering = ["date"]
        constraints = [
            models.UniqueConstraint(
                fields=["interpreter", "date"],
                name="one_exception_per_date",
            ),
        ]

    def __str__(self):
        return f"{self.interpreter.user.full_name} - {self.date}"


class CertificationDocument(models.Model):
    interpreter = models.ForeignKey(
        InterpreterProfile,
        on_delete=models.CASCADE,
        related_name="certifications",
    )
    document = models.FileField(
        upload_to=certification_upload_path,
        validators=[FileExtensionValidator(["pdf", "jpg", "jpeg", "png"])],
    )
    document_name = models.CharField(max_length=255)
    certificate_type = models.CharField(max_length=100, blank=True, default="")
    issuing_organisation = models.CharField(max_length=255, blank=True, default="")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    rejection_reason = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return (
            f"Certification: {self.document_name} - {self.interpreter.user.full_name}"
        )
