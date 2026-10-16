"""Institutions created by a platform administrator (V2-005): temporary password and login-details email.

Used by the admin API (manage_views.py) and the Django admin site, so both behave the same way: the
institution is APPROVED at once, its administrator gets a temporary password by email and must choose
their own at first sign-in. If the email cannot be sent, nothing is created.
"""

import logging
import secrets

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import transaction

logger = logging.getLogger("apps.institutions")

# No look-alike characters (0/O, 1/I/L), so it can be copied from the email without mistakes.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_DIGITS = "23456789"


class CredentialsEmailError(Exception):
    """The login-details email could not be sent. The institution was not created."""


def generate_temporary_password(user):
    """Like K7PM-4Q9X. Meets the password rules (length, a number, a special character)."""
    while True:
        chars = [secrets.choice(_ALPHABET) for _ in range(8)]
        if not any(ch in _DIGITS for ch in chars):
            continue
        password = f"{''.join(chars[:4])}-{''.join(chars[4:])}"
        try:
            validate_password(password, user=user)
        except ValidationError:
            continue
        return password


def create_approved_institution(data, admin, request=None):
    """Create an APPROVED institution and email its login details. Returns the institution's user.

    `data` is the request body (validated by AdminInstitutionCreateSerializer). Raises the serializer's
    ValidationError for bad data, or CredentialsEmailError (and rolls everything back) if sending fails.
    """
    from apps.audit import services as audit
    from apps.audit.models import AuditLog

    from .serializers import AdminInstitutionCreateSerializer

    serializer = AdminInstitutionCreateSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    with transaction.atomic():
        user = serializer.save()
        try:
            _send_login_details(user, serializer.temporary_password)
        except Exception as exc:  # noqa: BLE001 - any mail failure: undo the account and say so
            logger.exception("institution_credentials_email_failed admin=%s", admin.pk)
            raise CredentialsEmailError from exc
        audit.record(AuditLog.Event.INSTITUTION_CREATED, admin, request)
    logger.info("institution_created_by_admin admin=%s institution=%s", admin.pk, user.institution_profile.pk)
    return user


def _send_login_details(user, temporary_password):
    institution = user.institution_profile
    body = (
        f"Hello {user.full_name},\n\n"
        f"A SignConnect administrator has created an account for {institution.organisation_name}, "
        "and you are its administrator.\n\n"
        f"Sign in with your email ({user.email}) or phone number ({user.phone_number}) and this temporary "
        f"password:\n\n    {temporary_password}\n\n"
        "You will be asked to choose your own password when you first sign in.\n"
        "If you were not expecting this email, contact the SignConnect team.\n"
    )
    send_mail("Your SignConnect institution account", body, settings.DEFAULT_FROM_EMAIL, [user.email])
