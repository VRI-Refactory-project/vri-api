import phonenumbers
from django.conf import settings
from django.contrib.auth import get_user_model


def normalize_phone(value):
    """Return the E.164 form of a phone number (e.g. '0772 123456' -> '+256772123456'),
    or None if it is not a valid number. Local numbers default to PHONENUMBER_DEFAULT_REGION."""
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
