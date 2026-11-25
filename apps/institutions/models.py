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
    DELETED = "DELETED", "Deleted"


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
    description = models.TextField(blank=True, default="")
    verification_document = models.FileField(
        upload_to="institution_documents/",
        blank=True,
        null=True,
    )
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


class InstitutionDepartment(models.Model):
    institution = models.ForeignKey(InstitutionProfile, on_delete=models.CASCADE, related_name="departments")
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(
                models.functions.Lower("name"),
                models.F("institution"),
                condition=models.Q(is_active=True),
                name="institution_department_name_ci_unique",
            )
        ]

    def __str__(self):
        return self.name


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
    department = models.ForeignKey(
        InstitutionDepartment,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="staff",
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


class InstitutionNotice(models.Model):
    """A notice shared with all active members of one institution."""

    institution = models.ForeignKey(
        InstitutionProfile,
        on_delete=models.CASCADE,
        related_name="notices",
    )
    title = models.CharField(max_length=150)
    message = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_institution_notices",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.title} ({self.institution.organisation_name})"


class TutorialProgress(models.Model):
    """How far a staff member got through the first-sign-in tutorial (V2-065).

    One row per membership, made the first time the tutorial is opened. The tutorial shows until it
    is finished or skipped; replaying it from Help starts it again.
    """

    staff = models.OneToOneField(
        InstitutionStaff, on_delete=models.CASCADE, related_name="tutorial"
    )
    step = models.PositiveSmallIntegerField(
        default=0, help_text="The step the person is on, counting from 0."
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    skipped_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def is_open(self):
        """True while the tutorial should still show by itself."""
        return self.completed_at is None and self.skipped_at is None

    def __str__(self):
        return f"Tutorial for {self.staff}"
