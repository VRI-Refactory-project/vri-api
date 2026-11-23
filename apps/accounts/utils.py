import phonenumbers
from django.conf import settings
from django.contrib.auth import get_user_model


def normalize_phone(value):
    """Return the E.164 form of a phone number (e.g. '0772 123456' -> '+256772123456'),
    or None if it is not a valid number. Local numbers default to PHONENUMBER_DEFAULT_REGION.
    """
    try:
        parsed = phonenumbers.parse(str(value), settings.PHONENUMBER_DEFAULT_REGION)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def find_user_by_identifier(identifier):
    """Find a user by email (if the identifier contains '@') or by phone number."""
    User = get_user_model()
    identifier = (identifier or "").strip()
    if "@" in identifier:
        return User.objects.filter(email__iexact=identifier).first()
    phone = normalize_phone(identifier)
    if phone is None:
        return None
    return User.objects.filter(phone_number=phone).first()


def is_unfinished_signup(user):
    """True for an interpreter who started signing up but never finished: the email was never
    confirmed, no certificate was uploaded and the account was never reviewed. Nobody has proved
    they own this email or phone number, so it must not block someone else from using them.
    """
    if user.role != user.Role.INTERPRETER or user.is_staff or user.is_superuser:
        return False
    if user.email_verified_at is not None:
        return False
    profile = getattr(user, "interpreter_profile", None)
    if profile is None:
        return True
    return (
        profile.verification_status == "INACTIVE"
        and not profile.certifications.exists()
    )


def unfinished_signups(email=None, phone=None):
    """The unfinished interpreter sign-ups that hold this email and/or phone number."""
    User = get_user_model()
    found = {}
    if email:
        for user in User.objects.filter(email__iexact=email):
            found[user.pk] = user
    if phone:
        for user in User.objects.filter(phone_number=phone):
            found[user.pk] = user
    return [user for user in found.values() if is_unfinished_signup(user)]


def email_in_use(email):
    """Is this email held by an account that really exists (not an unfinished sign-up)?"""
    User = get_user_model()
    return any(
        not is_unfinished_signup(user)
        for user in User.objects.filter(email__iexact=email)
    )


def phone_in_use(phone):
    """Is this phone number held by an account that really exists (not an unfinished sign-up)?"""
    User = get_user_model()
    return any(
        not is_unfinished_signup(user)
        for user in User.objects.filter(phone_number=phone)
    )
