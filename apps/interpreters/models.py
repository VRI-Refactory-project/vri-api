from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MaxValueValidator
from django.db import models

User = get_user_model()


class VerificationStatus(models.TextChoices):
    INACTIVE = "INACTIVE", "Inactive / Pending Review"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"
    # Switched off by an admin after approval (V2-045), e.g. a lapsed certificate. Not routed to.
    DEACTIVATED = "DEACTIVATED", "Deactivated"


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
    specialisations = models.JSONField(
        default=list,
        blank=True,
        help_text="Everything the interpreter covers, from the specialisation library "
        "(see specialisations.py). `specialisation` is the one used for routing.",
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
                check=(
                    models.Q(is_available=False)
                    | models.Q(verification_status=VerificationStatus.APPROVED)
                ),
                name="available_only_if_approved",
            ),
        ]

    def all_specialisations(self):
        """Everything this interpreter covers. Profiles saved before the library existed fall back
        to their single specialisation."""
        return list(self.specialisations) or [self.specialisation]

    def has_specialisation(self, value):
        return value in self.all_specialisations()

    def specialisation_labels(self):
        from .specialisations import label_for

        return [label_for(v, self.specialisation_other) for v in self.all_specialisations()]

    def clean(self):
        super().clean()
        if (
            self.verification_status == VerificationStatus.APPROVED
            and not self.user.email_verified
        ):
            raise ValidationError(
                {
                    "verification_status": "Cannot approve: this interpreter has not verified their email address."
                }
            )

    def __str__(self):
        return f"{self.user.full_name} ({self.get_verification_status_display()})"


class InterpreterAvailability(models.Model):
    interpreter = models.ForeignKey(
        InterpreterProfile,
        on_delete=models.CASCADE,
        related_name="availability_periods",
    )
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["start_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_at__gt=models.F("start_at")),
                name="availability_end_after_start",
            ),
        ]
        indexes = [
            models.Index(
                fields=["interpreter", "start_at", "end_at"],
                name="interp_avail_period_idx",
            ),
        ]

    def __str__(self):
        return f"{self.interpreter.user.full_name}: {self.start_at} - {self.end_at}"


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


class VerificationDecision(models.Model):
    """One admin decision on an interpreter's verification: the V2-045 audit log.

    Rows are never edited or deleted. Both foreign keys use PROTECT so the history survives;
    deactivate a user instead of deleting them.
    """

    class Action(models.TextChoices):
        APPROVE = "approve", "Approved"
        REJECT = "reject", "Rejected"
        DEACTIVATE = "deactivate", "Deactivated"
        REACTIVATE = "reactivate", "Reactivated"

    interpreter = models.ForeignKey(
        InterpreterProfile,
        on_delete=models.PROTECT,
        related_name="decisions",
    )
    admin = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
    )
    action = models.CharField(max_length=20, choices=Action.choices)
    reason = models.TextField(blank=True, default="")
    previous_status = models.CharField(
        max_length=20, choices=VerificationStatus.choices
    )
    new_status = models.CharField(max_length=20, choices=VerificationStatus.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.get_action_display()}: {self.interpreter.user.full_name} by {self.admin.full_name}"


class AvailabilityPeriod(models.Model):
    interpreter = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="availabilities"
    )
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()

    class Meta:
        ordering = ["start_time"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(end_time__gt=models.F("start_time")),
                name="check_end_time_after_start_time",
            )
        ]

    def __str__(self):
        return f"{self.interpreter.username}: {self.start_time} to {self.end_time}"

    def clean(self):
        # Prevent overlapping availability periods for the same interpreter
        overlapping = AvailabilityPeriod.objects.filter(
            interpreter=self.interpreter,
            start_time__lt=self.end_time,
            end_time__gt=self.start_time,
        )
        if self.pk:
            overlapping = overlapping.exclude(pk=self.pk)

        if overlapping.exists():
            raise ValidationError(
                "This period overlaps with an existing availability slot."
            )
