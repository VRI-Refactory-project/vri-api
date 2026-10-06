from .models import InterpreterProfile, VerificationStatus


def get_eligible_interpreters(specialisation_required=None, allow_fallback=False):
    """
    Returns a queryset of interpreters eligible to receive call routing.
    Blocks all unverified, inactive, or unavailable interpreters.

    If specialisation_required is given, only matching specialists are
    returned. Set allow_fallback=True to get general interpreters when
    no specialist is available.
    """
    eligible_queryset = InterpreterProfile.objects.filter(
        verification_status=VerificationStatus.APPROVED,
        user__is_active=True,
        is_available=True,
    ).select_related("user")

    if not specialisation_required:
        return eligible_queryset

    specialists = eligible_queryset.filter(specialisation=specialisation_required)

    if specialists.exists() or not allow_fallback:
        return specialists

    return eligible_queryset
