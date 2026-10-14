from django.utils import timezone

from .availability import is_available_now
from .models import InterpreterProfile, VerificationStatus


def get_eligible_interpreters(
    specialisation_required=None, allow_fallback=False, now=None
):
    """
    Returns a queryset of interpreters eligible to receive call routing.
    Blocks all unverified, inactive, or unavailable interpreters.

    "Unavailable" means the dashboard switch (is_available) is off, OR the
    interpreter is outside their hours right now (date exception first, then
    their default week).

    If specialisation_required is given, only matching specialists are
    returned. Set allow_fallback=True to get general interpreters when
    no specialist is available.
    """
    now = now or timezone.localtime()

    switched_on = InterpreterProfile.objects.filter(
        verification_status=VerificationStatus.APPROVED,
        user__is_active=True,
        is_available=True,
    ).select_related("user")

    # Keep only interpreters whose schedule allows a request at this moment.
    in_hours_ids = [
        profile.pk for profile in switched_on if is_available_now(profile, now)
    ]
    eligible_queryset = switched_on.filter(pk__in=in_hours_ids)

    if not specialisation_required:
        return eligible_queryset

    specialists = eligible_queryset.filter(specialisation=specialisation_required)

    if specialists.exists() or not allow_fallback:
        return specialists

    return eligible_queryset
