"""Account recovery (V2-008): email codes, admin-assisted resets and signing users out everywhere.

Views stay thin; all the rules live here so they are easy to test and to extend (e.g. SMS later).
"""

import json
import logging
import secrets
import urllib.error
import urllib.request
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)

from .models import RecoveryCode

logger = logging.getLogger("apps.accounts.recovery")

RESEND_COOLDOWN_SECONDS = 60
MAX_CODES_PER_HOUR = 5
MAX_CODES_PER_DAY = 10
MAX_FAILED_ATTEMPTS_PER_DAY = (
    15  # across all codes; after this, recovery is locked for 24 hours
)
# Used to spend the same hashing time when there is no code to check (unknown account).
_DUMMY_HASH = make_password("000000")


def _generate_code():
    return f"{secrets.randbelow(1_000_000):06d}"


def is_locked(user, purpose):
    """Too many wrong codes for this account in the last 24 hours."""
    since = timezone.now() - timedelta(days=1)
    failed = (
        RecoveryCode.objects.filter(
            user=user, purpose=purpose, created_at__gte=since
        ).aggregate(total=Sum("attempts"))["total"]
        or 0
    )
    return failed >= MAX_FAILED_ATTEMPTS_PER_DAY


def issue_code(user, purpose, target_user=None, ip=None):
    """Create a new code for `user` (older ones stop working) and email it.

    Returns "sent", or why nothing was sent: "cooldown", "limited", "locked" or "failed".
    """
    now = timezone.now()
    history = RecoveryCode.objects.filter(user=user, purpose=purpose)
    if history.filter(
        target_user=target_user,
        used_at__isnull=True,
        created_at__gte=now - timedelta(seconds=RESEND_COOLDOWN_SECONDS),
    ).exists():
        reason = "cooldown"
    elif (
        history.filter(created_at__gte=now - timedelta(hours=1)).count()
        >= MAX_CODES_PER_HOUR
        or history.filter(created_at__gte=now - timedelta(days=1)).count()
        >= MAX_CODES_PER_DAY
    ):
        reason = "limited"
    elif is_locked(user, purpose):
        reason = "locked"
    else:
        reason = None
    if reason:
        logger.warning(
            "recovery_code_not_sent reason=%s user=%s purpose=%s ip=%s",
            reason,
            user.pk,
            purpose,
            ip,
        )
        return reason

    code = _generate_code()
    with transaction.atomic():
        # Older codes stop working but are kept, so the hourly/daily limits can count them.
        history.filter(
            target_user=target_user, used_at__isnull=True, expires_at__gt=now
        ).update(expires_at=now)
        rc = RecoveryCode.objects.create(
            user=user,
            target_user=target_user,
            purpose=purpose,
            code_hash=make_password(code),
            expires_at=now + RecoveryCode.LIFETIME,
            requested_ip=ip,
        )
    try:
        _send_code_email(user, code, purpose, target_user)
    except Exception:
        rc.delete()
        logger.exception("recovery_email_failed user=%s purpose=%s", user.pk, purpose)
        return "failed"
    logger.info(
        "recovery_code_sent user=%s purpose=%s target=%s ip=%s",
        user.pk,
        purpose,
        getattr(target_user, "pk", None),
        ip,
    )
    return "sent"


def check_code(user, code, purpose, target_user=None):
    """Return the matching usable RecoveryCode, or None. A wrong guess counts as an attempt."""
    if user is None or is_locked(user, purpose):
        check_password(code, _DUMMY_HASH)
        return None
    rc = (
        RecoveryCode.objects.filter(
            user=user, purpose=purpose, target_user=target_user, used_at__isnull=True
        )
        .order_by("-created_at")
        .first()
    )
    if (
        rc is None
        or rc.expires_at <= timezone.now()
        or rc.attempts >= RecoveryCode.MAX_ATTEMPTS
    ):
        check_password(code, _DUMMY_HASH)
        return None
    if not check_password(code, rc.code_hash):
        RecoveryCode.objects.filter(pk=rc.pk).update(attempts=F("attempts") + 1)
        logger.info("recovery_code_wrong user=%s purpose=%s", user.pk, purpose)
        return None
    return rc


def mark_used(recovery_code):
    """Atomically claim the code. Returns False if another request used it first."""
    return (
        RecoveryCode.objects.filter(pk=recovery_code.pk, used_at__isnull=True).update(
            used_at=timezone.now()
        )
        == 1
    )


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
    RecoveryCode.objects.filter(
        user=user,
        purpose=RecoveryCode.Purpose.SELF_RECOVERY,
        used_at__isnull=True,
        expires_at__gt=timezone.now(),
    ).update(expires_at=timezone.now())
    sign_out_everywhere(user)


def mark_email_verified(user):
    """Record that the user proved they own their email."""
    user.email_verified_at = timezone.now()
    user.save(update_fields=["email_verified_at"])


def mask_email(email):
    name, _, domain = email.partition("@")
    return f"{name[:2]}{'*' * max(len(name) - 2, 1)}@{domain}"


def _send_code_email(user, code, purpose, target_user=None):
    minutes = int(RecoveryCode.LIFETIME.total_seconds() // 60)

    # --- BREVO SETTINGS ---
    BREVO_API_KEY = getattr(settings, "BREVO_API_KEY", "")
    template_id = 2
    # ----------------------

    if purpose == RecoveryCode.Purpose.EMAIL_VERIFY:
        subject = "Verify your SignConnect email"
    elif purpose == RecoveryCode.Purpose.SELF_RECOVERY:
        subject = "Your SignConnect recovery code"
    else:
        subject = "Confirm a SignConnect password reset"

    url = "https://api.brevo.com/v3/smtp/email"

    payload = {
        "sender": {"name": "SignConnect", "email": settings.DEFAULT_FROM_EMAIL},
        "to": [{"email": user.email, "name": user.full_name}],
        "subject": subject,
        "templateId": template_id,
        "params": {
            "code": code,
            "minutes": minutes,
        },
    }

    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"))
    req.add_header("accept", "application/json")
    req.add_header("api-key", BREVO_API_KEY)
    req.add_header("content-type", "application/json")

    try:
        with urllib.request.urlopen(req):
            pass  # Success
    except urllib.error.HTTPError as e:
        logger.error(f"Brevo API Error: {e.read().decode()}")
        raise RuntimeError("Failed to send email via Brevo") from e
