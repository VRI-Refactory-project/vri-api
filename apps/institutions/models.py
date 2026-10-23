from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class OrganisationType(models.TextChoices):
    HOSPITAL = "HOSPITAL", "Hospital / health facility"
    COURT = "COURT", "Court / legal"
    GOVERNMENT = "GOVERNMENT", "Government office"
    SCHOOL = "SCHOOL", "School or Institution"
    POLICE = "POLICE", "Police Station"
    CHURCH = "CHURCH", "Church"
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
    organisation_type = models.CharField(
        max_length=20, choices=OrganisationType.choices
    )
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
                {
                    "status": "Cannot approve: the registrant has not verified their email address."
                }
            )

    def __str__(self):
        return f"{self.organisation_name} ({self.get_status_display()})"


class StaffRole(models.TextChoices):
    """What a person may do inside their institution (SRS 2.4)."""

    ADMIN = "ADMIN", "Institution administrator"  # everything, including managing staff
    STAFF = (
        "STAFF",
        "Institutional user",
    )  # sessions, interpreters, history; no staff management


class InstitutionStaff(models.Model):
    """A person's membership of an institution (V2-006, SRS 8.1 "Institution Staff").

    Every institution user has one, including the person who registered the institution (an ADMIN).
    Revoking keeps the row (and the account) so history still shows who did what; the account is
    deactivated, so the person cannot sign in.
    """

    institution = models.ForeignKey(
        InstitutionProfile, on_delete=models.CASCADE, related_name="staff"
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="institution_staff",
    )
    role = models.CharField(
        max_length=10, choices=StaffRole.choices, default=StaffRole.STAFF
    )
    job_title = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="e.g. Doctor, Police officer. Shown only; not a permission.",
    )
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    revoked_at = models.DateTimeField(
        null=True, blank=True, help_text="Empty while the person has access."
    )
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name_plural = "institution staff"
        indexes = [
            models.Index(fields=["institution", "is_active"]),
        ]

    def __str__(self):
        return f"{self.user.full_name} ({self.get_role_display()}, {self.institution.organisation_name})"

    @property
    def is_revoked(self):
        return self.revoked_at is not None
