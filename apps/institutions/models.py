from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class OrganisationType(models.TextChoices):
    HOSPITAL = "HOSPITAL", "Hospital / health facility"
    COURT = "COURT", "Court / legal"
    GOVERNMENT = "GOVERNMENT", "Government office"
    OTHER = "OTHER", "Other"


class InstitutionStatus(models.TextChoices):
    PENDING = "PENDING", "Pending approval"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"


class InstitutionProfile(models.Model):
    """An institution account (V2-005). The user who registers it is its designated administrator
    and can later create staff accounts (V2-006). It stays PENDING until a platform admin approves it
    (V2-046)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="institution_profile",
    )
    organisation_name = models.CharField(max_length=200)
    organisation_type = models.CharField(max_length=20, choices=OrganisationType.choices)
    # What the institution is, in the registrant's own words. Only filled when the type is OTHER.
    organisation_type_other = models.CharField(max_length=100, blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=InstitutionStatus.choices,
        default=InstitutionStatus.PENDING,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        super().clean()
        if self.status == InstitutionStatus.APPROVED and not self.user.email_verified:
            raise ValidationError(
                {"status": "Cannot approve: the registrant has not verified their email address."}
            )

    def __str__(self):
        return f"{self.organisation_name} ({self.get_status_display()})"
