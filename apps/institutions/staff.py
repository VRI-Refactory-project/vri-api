"""Institution staff accounts (V2-006): create, change, revoke and reinstate, done by an institution's admins.

New staff get a temporary password by email ("send account details to staff" in the user flow) and must choose
their own at first sign-in. Revoking deactivates the account and signs it out everywhere, so it is blocked at
once (see docs/design/V2-006-staff-accounts.md).
"""

import logging
import secrets

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.accounts import recovery
from apps.audit import services as audit
from apps.audit.models import AuditLog

from .models import InstitutionStaff, StaffRole

logger = logging.getLogger("apps.institutions")

# No look-alike characters (0/O, 1/I/L), so it can be copied from the email without mistakes.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_DIGITS = "23456789"


class StaffEmailError(Exception):
    """The login-details email could not be sent. The staff account was not created."""


def generate_temporary_password(user):
    """Like K7PM-4Q9X. Meets the password rules (length, a number, a special character)."""
    while True:
        chars = [secrets.choice(_ALPHABET) for _ in range(8)]
        if not any(ch in _DIGITS for ch in chars):
            continue
        password = f"{''.join(chars[:4])}-{''.join(chars[4:])}"
        try:
            validate_password(password, user=user)
        except DjangoValidationError:
            continue
        return password


def create_staff(serializer, admin, request=None):
    """Save a validated StaffCreateSerializer, email the login details and audit it. Returns the membership.

    Everything is undone (StaffEmailError) if the email cannot be sent, so no account exists whose password
    nobody knows.
    """
    with transaction.atomic():
        membership = serializer.save()
        user = membership.user
        password = generate_temporary_password(user)
        user.set_password(password)
        user.must_change_password = True
        user.save(update_fields=["password", "must_change_password"])
        try:
            _send_login_details(membership, admin, password)
        except Exception as exc:  # noqa: BLE001 - any mail failure: undo the account and say so
            logger.exception("staff_credentials_email_failed admin=%s", admin.pk)
            raise StaffEmailError from exc
        audit.record(AuditLog.Event.STAFF_CREATED, admin, request, target_user=user)
    return membership


def update_staff(membership, data, admin, request=None):
    """Change a staff member's name, job title and/or role. `data` is validated StaffUpdateSerializer data."""
    new_role = data.get("role", membership.role)
    if membership.role == StaffRole.ADMIN and new_role != StaffRole.ADMIN:
        _keep_an_admin(membership)
    with transaction.atomic():
        if "full_name" in data:
            membership.user.full_name = data["full_name"]
            membership.user.save(update_fields=["full_name"])
        membership.job_title = data.get("job_title", membership.job_title)
        membership.role = new_role
        membership.save(update_fields=["job_title", "role", "updated_at"])
        audit.record(AuditLog.Event.STAFF_UPDATED, admin, request, target_user=membership.user)
    return membership


def revoke_staff(membership, admin, request=None):
    """Remove a staff member's access at once: the account is deactivated and every session is ended.

    Their current access token stops working on the next request too: deactivated users fail authentication.
    """
    if membership.user_id == admin.pk:
        raise ValidationError({"detail": "You cannot revoke your own access."})
    if membership.is_revoked:
        return membership
    if membership.role == StaffRole.ADMIN:
        _keep_an_admin(membership)
    with transaction.atomic():
        membership.revoked_at = timezone.now()
        membership.revoked_by = admin
        membership.save(update_fields=["revoked_at", "revoked_by", "updated_at"])
        membership.user.is_active = False
        membership.user.save(update_fields=["is_active"])
        recovery.sign_out_everywhere(membership.user)
        audit.record(AuditLog.Event.STAFF_REVOKED, admin, request, target_user=membership.user)
    logger.info("staff_revoked admin=%s user=%s institution=%s", admin.pk, membership.user_id,
                membership.institution_id)
    return membership


def reinstate_staff(membership, admin, request=None):
    """Give a revoked staff member access again, with their existing password and role."""
    if not membership.is_revoked:
        return membership
    with transaction.atomic():
        membership.revoked_at = None
        membership.revoked_by = None
        membership.save(update_fields=["revoked_at", "revoked_by", "updated_at"])
        membership.user.is_active = True
        membership.user.save(update_fields=["is_active"])
        audit.record(AuditLog.Event.STAFF_REINSTATED, admin, request, target_user=membership.user)
    return membership


def _keep_an_admin(membership):
    """An institution must always keep at least one active administrator, or nobody could manage its staff."""
    other_admins = InstitutionStaff.objects.filter(
        institution=membership.institution, role=StaffRole.ADMIN, revoked_at__isnull=True,
    ).exclude(pk=membership.pk)
    if not other_admins.exists():
        raise ValidationError({"detail": "Your institution must keep at least one active administrator. "
                                         "Make someone else an administrator first."})


def _send_login_details(membership, admin, temporary_password):
    user = membership.user
    body = (
        f"Hello {user.full_name},\n\n"
        f"{admin.full_name} has given you a SignConnect account at {membership.institution.organisation_name} "
        f"({membership.get_role_display()}).\n\n"
        f"Sign in with your email ({user.email}) or phone number ({user.phone_number}) and this temporary "
        f"password:\n\n    {temporary_password}\n\n"
        "You will be asked to choose your own password when you first sign in.\n"
        "If you were not expecting this email, contact your institution's administrator.\n"
    )
    send_mail("Your SignConnect staff account", body, settings.DEFAULT_FROM_EMAIL, [user.email])
