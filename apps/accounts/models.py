from datetime import timedelta

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone
from phonenumber_field.modelfields import PhoneNumberField


class UserManager(BaseUserManager):
    """Creates users identified by phone number (stored in E.164, e.g. +256772123456)."""

    use_in_migrations = True

    def _create_user(self, phone_number, password, **extra_fields):
        if not phone_number:
            raise ValueError("A phone number is required.")
        if extra_fields.get("role") not in self.model.Role.values:
            raise ValueError(f"A valid role is required, one of: {', '.join(self.model.Role.values)}.")
        email = extra_fields.pop("email", None)
        extra_fields["email"] = self.normalize_email(email).lower() if email else None
        user = self.model(phone_number=phone_number, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, phone_number, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(phone_number, password, **extra_fields)

    def create_superuser(self, phone_number, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", User.Role.ADMIN)
        if not extra_fields["is_staff"] or not extra_fields["is_superuser"]:
            raise ValueError("A superuser must have is_staff=True and is_superuser=True.")
        return self._create_user(phone_number, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """One account table for every role. Role-specific data lives in one-to-one profile
    models in each role's app (DeafUserProfile, InterpreterProfile, InstitutionProfile).

    Login: phone number OR email, plus password/PIN (see apps.accounts.backends).
    """

    class Role(models.TextChoices):
        DEAF_HOH = "deaf_hoh", "Deaf / Hard of Hearing"
        INTERPRETER = "interpreter", "Interpreter"
        INSTITUTION = "institution", "Institution"
        ADMIN = "admin", "Administrator"

    # Roles a new user can pick when they first open the platform. Admins are only created by staff.
    SELF_REGISTRATION_ROLES = (Role.DEAF_HOH, Role.INTERPRETER, Role.INSTITUTION)

    full_name = models.CharField(max_length=150)
    phone_number = PhoneNumberField(
        unique=True,
        help_text="Stored in international format, e.g. +256772123456. Local numbers default to Uganda.",
    )
    # Optional for most roles, required for interpreters (enforced in their registration serializer).
    # NULL (not "") when empty, so the unique constraint ignores users without an email.
    email = models.EmailField(unique=True, null=True, blank=True)
    role = models.CharField(max_length=20, choices=Role.choices)

    is_active = models.BooleanField(
        default=True,
        help_text="Untick to deactivate the account. Deactivated users cannot sign in.",
    )
    is_staff = models.BooleanField(default=False, help_text="Can log in to the Django admin site.")
    must_change_password = models.BooleanField(
        default=False,
        help_text="Set after an admin reset. The app should ask the user to choose a new password/PIN.",
    )
    date_joined = models.DateTimeField(default=timezone.now)
    terms_accepted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the user accepted the Terms and Privacy Policy at sign-up. Empty if they never did.",
    )

    objects = UserManager()

    USERNAME_FIELD = "phone_number"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        ordering = ["-date_joined"]

    def __str__(self):
        return f"{self.full_name} ({self.phone_number})"

    def save(self, *args, **kwargs):
        if self.email:
            self.email = self.email.strip().lower()
        else:
            self.email = None
        super().save(*args, **kwargs)


class RecoveryCode(models.Model):
    """A short-lived 6-digit code sent by email (V2-008 account recovery).

    - self_recovery: sent to a user who forgot their password/PIN.
    - admin_reset: sent to an ADMIN to confirm they want to reset another user's password/PIN
      (for users without an email). `target_user` is the account being reset.

    Only a hash of the code is stored. A code is usable once, for 15 minutes, with 5 wrong tries.
    """

    LIFETIME = timedelta(minutes=15)
    MAX_ATTEMPTS = 5

    class Purpose(models.TextChoices):
        SELF_RECOVERY = "self_recovery", "Self recovery"
        ADMIN_RESET = "admin_reset", "Admin-assisted reset"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="recovery_codes")
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="+"
    )
    purpose = models.CharField(max_length=20, choices=Purpose.choices)
    channel = models.CharField(max_length=10, default="email")
    code_hash = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    used_at = models.DateTimeField(null=True, blank=True)
    requested_ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "purpose", "used_at"])]

    def __str__(self):
        return f"{self.get_purpose_display()} code for {self.user} ({self.created_at:%Y-%m-%d %H:%M})"
