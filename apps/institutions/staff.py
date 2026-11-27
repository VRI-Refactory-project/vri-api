"""Institution staff accounts (V2-006): onboarding, changes, revocation and reinstatement."""

import logging
import secrets

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.accounts import recovery
from apps.audit import services as audit
from apps.audit.models import AuditLog
from apps.core.emailing import branded_html, send_branded_email

from .alerts import staff_member_added
from .models import InstitutionStaff, StaffRole

logger = logging.getLogger("apps.institutions")


class StaffEmailError(Exception):
    """The login details email could not be sent. The staff account was not created."""


def create_staff(serializer, admin, request=None):
    """Save a validated staff account, email its temporary password and audit it.

    The account is rolled back if the email cannot be sent.
    """
    with transaction.atomic():
        membership = serializer.save()
        user = membership.user
        temporary_password = secrets.token_urlsafe(24)
        user.set_password(temporary_password)
        user.must_change_password = True
        user.email_verified_at = timezone.now()
        user.save(
            update_fields=["password", "must_change_password", "email_verified_at"]
        )
        try:
            _send_login_details(membership, admin, temporary_password)
        except Exception as exc:
            logger.exception("staff_login_email_failed admin=%s", admin.pk)
            raise StaffEmailError from exc
        audit.record(AuditLog.Event.STAFF_CREATED, admin, request, target_user=user)
        staff_member_added(membership)
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
        audit.record(
            AuditLog.Event.STAFF_UPDATED, admin, request, target_user=membership.user
        )
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
        audit.record(
            AuditLog.Event.STAFF_REVOKED, admin, request, target_user=membership.user
        )
    logger.info(
        "staff_revoked admin=%s user=%s institution=%s",
        admin.pk,
        membership.user_id,
        membership.institution_id,
    )
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
        audit.record(
            AuditLog.Event.STAFF_REINSTATED, admin, request, target_user=membership.user
        )
    return membership


def update_own_profile(membership, data, request=None):
    """A staff member changes their own name and/or phone number. `data` is validated
    OwnProfileUpdateSerializer data."""
    if not data:
        return membership
    user = membership.user
    with transaction.atomic():
        for field, value in data.items():
            setattr(user, field, value)
        user.save(update_fields=list(data))
        audit.record(AuditLog.Event.STAFF_UPDATED, user, request, target_user=user)
    return membership


def _keep_an_admin(membership):
    """An institution must always keep at least one active administrator, or nobody could manage its staff."""
    other_admins = InstitutionStaff.objects.filter(
        institution=membership.institution,
        role=StaffRole.ADMIN,
        revoked_at__isnull=True,
    ).exclude(pk=membership.pk)
    if not other_admins.exists():
        raise ValidationError(
            {
                "detail": "Your institution must keep at least one active administrator. "
                "Make someone else an administrator first."
            }
        )


def _send_login_details(membership, admin, temporary_password):
    user = membership.user
    login_url = f"{settings.FRONTEND_URL.rstrip('/')}/sign-in"
    body = (
        f"Hello {user.full_name},\n\n"
        f"{admin.full_name} has given you a SignConnect account at {membership.institution.organisation_name} "
        f"({membership.get_role_display()}).\n\n"
        f"Sign in with your email ({user.email}) or phone number ({user.phone_number}) and this temporary "
        f"password:\n\n    {temporary_password}\n\n"
        "You may change this password after signing in, or later in Settings.\n\n"
        f"Log in: {login_url}\n\n"
        "If you were not expecting this email, contact your institution's administrator.\n"
    )
    html = branded_html(body, highlight=temporary_password).replace(
        f"Log in: {login_url}", f'<a href="{login_url}">Log in</a>'
    )
    send_branded_email(
        "Your SignConnect staff account login details",
        body,
        html,
        user.email,
        user.full_name,
        log=logger,
    )
