from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models


class VerificationStatus(models.TextChoices):
    INACTIVE = "INACTIVE", "Inactive / Pending Review"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"


class Specialisation(models.TextChoices):
    GENERAL = "GENERAL", "General"
    MEDICAL = "MEDICAL", "Medical"
    LEGAL = "LEGAL", "Legal"


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
    working_hours_schedule = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [  # noqa: RUF012
            models.CheckConstraint(
                check=models.Q(is_available=False)
                | models.Q(verification_status=VerificationStatus.APPROVED),
                name="available_only_if_approved",
            ),
        ]

    def clean(self):
        super().clean()
        if self.verification_status == VerificationStatus.APPROVED and not self.user.email_verified:
            raise ValidationError(
                {"verification_status": "Cannot approve: this interpreter has not verified their email address."}
            )

    def __str__(self):
        return f"{self.user.full_name} ({self.get_verification_status_display()})"


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
    # What the certificate is and who issued it (asked for on the web upload page, V2-003).
    # Blank defaults keep existing rows valid; the API requires both for new uploads.
    certificate_type = models.CharField(max_length=100, blank=True, default="")
    issuing_organisation = models.CharField(max_length=255, blank=True, default="")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    rejection_reason = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-uploaded_at"]  # noqa: RUF012

    def __str__(self):
        return f"Certification: {self.document_name} - {self.interpreter.user.full_name}"
