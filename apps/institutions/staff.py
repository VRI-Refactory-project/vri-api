"""Institution staff accounts (V2-006): onboarding, changes, revocation and reinstatement."""

import logging
import secrets
from urllib.parse import quote

from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.hashers import check_password, make_password
from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.accounts import recovery
from apps.audit import services as audit
from apps.audit.models import AuditLog
from apps.core.emailing import branded_html, send_branded_email

from .alerts import staff_member_added
from .models import (
    InstitutionStaff,
    InstitutionStaffInvitation,
    InstitutionStatus,
    StaffRole,
)

logger = logging.getLogger("apps.institutions")

INVITATION_TOKEN_INVALID = "This invitation is invalid, expired, or has already been used."


class StaffEmailError(Exception):
    """The invitation email could not be sent. The staff account was not created."""


def create_staff(serializer, admin, request=None):
    """Save a validated StaffCreateSerializer, email an invitation and audit it.

    The invitation and account are rolled back if the email cannot be sent.
    """
    with transaction.atomic():
        membership = serializer.save()
        user = membership.user
        user.must_change_password = True
        user.save(update_fields=["must_change_password"])
        try:
            issue_staff_invitation(membership, admin)
        except Exception as exc:
            logger.exception("staff_invitation_email_failed admin=%s", admin.pk)
            raise StaffEmailError from exc
        audit.record(AuditLog.Event.STAFF_CREATED, admin, request, target_user=user)
        staff_member_added(membership)
    return membership


@transaction.atomic
def issue_staff_invitation(membership, admin):
    """Invalidate previous invitations and send a hashed, expiring setup link."""
    now = timezone.now()
    token_selector = secrets.token_urlsafe(9)
    token_secret = secrets.token_urlsafe(32)
    token = f"{token_selector}.{token_secret}"
    InstitutionStaffInvitation.objects.filter(
        staff=membership, used_at__isnull=True
    ).update(expires_at=now)
    InstitutionStaffInvitation.objects.create(
        staff=membership,
        token_selector=token_selector,
        token_hash=make_password(token_secret),
        expires_at=now + InstitutionStaffInvitation.LIFETIME,
    )
    link = (
        f"{settings.FRONTEND_URL.rstrip('/')}/staff/accept-invitation?"
        f"token={quote(token, safe='')}"
    )
    user = membership.user
    body = (
        f"Hello {user.full_name},\n\n"
        f"{admin.full_name} has invited you to join SignConnect at "
        f"{membership.institution.organisation_name} ({membership.get_role_display()}).\n\n"
        "Set your password using this secure invitation link. It expires in 24 hours and can only be used once:\n\n"
        f"{link}\n\n"
        "If you were not expecting this invitation, contact your institution's administrator.\n"
    )
    send_branded_email(
        "Set up your SignConnect staff account",
        body,
        branded_html(body, highlight=link),
        user.email,
        user.full_name,
        log=logger,
    )


@transaction.atomic
def accept_staff_invitation(token, new_password, ip=None):
    """Consume an active staff invitation and return the newly authenticated user."""
    try:
        selector, secret = token.split(".", 1)
    except (AttributeError, ValueError):
        raise ValidationError({"token": [INVITATION_TOKEN_INVALID]})

    try:
        invitation = (
            InstitutionStaffInvitation.objects.select_for_update()
            .select_related("staff__user", "staff__institution")
            .get(token_selector=selector)
        )
    except InstitutionStaffInvitation.DoesNotExist:
        raise ValidationError({"token": [INVITATION_TOKEN_INVALID]})

    now = timezone.now()
    membership = invitation.staff
    user = membership.user
    valid = (
        invitation.used_at is None
        and invitation.expires_at > now
        and membership.is_active
        and membership.revoked_at is None
        and membership.institution.status == InstitutionStatus.APPROVED
        and user.is_active
        and check_password(secret, invitation.token_hash)
    )
    if not valid:
        raise ValidationError({"token": [INVITATION_TOKEN_INVALID]})

    try:
        validate_password(new_password, user=user)
    except DjangoValidationError as exc:
        raise ValidationError({"new_password": list(exc.messages)})

    user.set_password(new_password)
    user.must_change_password = False
    user.save(update_fields=["password", "must_change_password"])
    invitation.used_at = now
    invitation.save(update_fields=["used_at"])

    result = recovery.issue_code(
        user, recovery.RecoveryCode.Purpose.EMAIL_VERIFY, ip=ip
    )
    if result != "sent":
        raise ValidationError(
            {"detail": "We could not send your email verification code. Please try again."}
        )
    return user


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
    body = (
        f"Hello {user.full_name},\n\n"
        f"{admin.full_name} has given you a SignConnect account at {membership.institution.organisation_name} "
        f"({membership.get_role_display()}).\n\n"
        f"Sign in with your email ({user.email}) or phone number ({user.phone_number}) and this temporary "
        f"password:\n\n    {temporary_password}\n\n"
        "You will be asked to choose your own password when you first sign in.\n"
        "If you were not expecting this email, contact your institution's administrator.\n"
    )
    send_branded_email(
        "Your SignConnect staff account",
        body,
        branded_html(body, highlight=temporary_password),
        user.email,
        user.full_name,
        log=logger,
    )
