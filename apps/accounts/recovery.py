"""Account recovery (V2-008): email codes, admin-assisted resets and signing users out everywhere.

Views stay thin; all the rules live here so they are easy to test and to extend (e.g. SMS later).
"""

import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from .models import RecoveryCode

logger = logging.getLogger("apps.accounts.recovery")

RESEND_COOLDOWN_SECONDS = 60
# Used to spend the same hashing time when there is no code to check (unknown account).
_DUMMY_HASH = make_password("000000")


def _generate_code():
    return f"{secrets.randbelow(1_000_000):06d}"


def issue_code(user, purpose, target_user=None, ip=None):
    """Create a new code for `user`, replacing older unused ones, and email it.

    Returns False (and sends nothing) if a code was sent less than RESEND_COOLDOWN_SECONDS ago.
    """
    now = timezone.now()
    open_codes = RecoveryCode.objects.filter(user=user, purpose=purpose, target_user=target_user, used_at__isnull=True)
    if open_codes.filter(created_at__gte=now - timedelta(seconds=RESEND_COOLDOWN_SECONDS)).exists():
        logger.info("recovery_code_cooldown user=%s purpose=%s", user.pk, purpose)
        return False

    code = _generate_code()
    with transaction.atomic():
        open_codes.delete()  # only the newest code is ever valid
        RecoveryCode.objects.create(
            user=user,
            target_user=target_user,
            purpose=purpose,
            code_hash=make_password(code),
            expires_at=now + RecoveryCode.LIFETIME,
            requested_ip=ip,
        )
    _send_code_email(user, code, purpose, target_user)
    logger.info("recovery_code_sent user=%s purpose=%s target=%s ip=%s",
                user.pk, purpose, getattr(target_user, "pk", None), ip)
    return True


def check_code(user, code, purpose, target_user=None):
    """Return the matching usable RecoveryCode, or None. A wrong guess counts as an attempt."""
    if user is None:
        check_password(code, _DUMMY_HASH)
        return None
    rc = (RecoveryCode.objects
          .filter(user=user, purpose=purpose, target_user=target_user, used_at__isnull=True)
          .order_by("-created_at").first())
    if rc is None or rc.expires_at <= timezone.now() or rc.attempts >= RecoveryCode.MAX_ATTEMPTS:
        check_password(code, _DUMMY_HASH)
        return None
    if not check_password(code, rc.code_hash):
        RecoveryCode.objects.filter(pk=rc.pk).update(attempts=F("attempts") + 1)
        logger.info("recovery_code_wrong user=%s purpose=%s", user.pk, purpose)
        return None
    return rc


def mark_used(recovery_code):
    recovery_code.used_at = timezone.now()
    recovery_code.save(update_fields=["used_at"])


def sign_out_everywhere(user):
    """Blacklist every refresh token the user holds, so other devices must sign in again.

    Access tokens already issued keep working until they expire (30 minutes).
    """
    for token in OutstandingToken.objects.filter(user=user):
        BlacklistedToken.objects.get_or_create(token=token)


def set_new_password(user, new_password, *, must_change=False):
    """Change only the password (and the must-change flag). Role, profile and status are untouched."""
    user.set_password(new_password)
    user.must_change_password = must_change
    user.save(update_fields=["password", "must_change_password"])
    RecoveryCode.objects.filter(user=user, purpose=RecoveryCode.Purpose.SELF_RECOVERY, used_at__isnull=True).delete()
    sign_out_everywhere(user)


def mask_email(email):
    name, _, domain = email.partition("@")
    return f"{name[:2]}{'*' * max(len(name) - 2, 1)}@{domain}"


def _send_code_email(user, code, purpose, target_user=None):
    minutes = int(RecoveryCode.LIFETIME.total_seconds() // 60)
    if purpose == RecoveryCode.Purpose.SELF_RECOVERY:
        subject = "Your SignConnect recovery code"
        body = (
            f"Hello {user.full_name},\n\n"
            f"Your SignConnect account recovery code is: {code}\n\n"
            f"It expires in {minutes} minutes and can be used once.\n"
            "If you did not ask to recover your account, ignore this email. Your account has not changed.\n"
        )
    else:
        subject = "Confirm a SignConnect password reset"
        body = (
            f"Hello {user.full_name},\n\n"
            f"You asked to reset the password/PIN for {target_user.full_name} ({target_user.phone_number}).\n"
            f"Your confirmation code is: {code}\n\n"
            f"It expires in {minutes} minutes. If you did not request this, tell the tech lead immediately.\n"
        )
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [user.email])
