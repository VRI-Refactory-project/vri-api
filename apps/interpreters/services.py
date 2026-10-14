
from django.utils import timezone

from .models import InterpreterProfile, VerificationStatus

from .models import (
    InterpreterAvailability,
    InterpreterProfile,
    VerificationStatus,
)


def get_eligible_interpreters(
    specialisation_required=None,
    allow_fallback=False,
    requested_at=None,
):
    """
    Return interpreters who are approved, active, available, and scheduled
    for the requested time.

    If requested_at is omitted, use the current time.
    Specialisation fallback is allowed only when explicitly requested.
    """
    requested_at = requested_at or timezone.now()

    eligible_queryset = (
        InterpreterProfile.objects.filter(
            verification_status=VerificationStatus.APPROVED,
            user__is_active=True,
            is_available=True,
            availability_periods__start_at__lte=requested_at,
            availability_periods__end_at__gt=requested_at,
        )
        .distinct()
        .select_related("user")
    )

    if not specialisation_required:
        return eligible_queryset

    specialists = eligible_queryset.filter(
        specialisation=specialisation_required
    )

    if specialists.exists() or not allow_fallback:
        return specialists

    return eligible_queryset
