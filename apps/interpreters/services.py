
from django.utils import timezone

from .availability import is_available_now
from .models import (
    InterpreterAvailability,
    InterpreterProfile,
    VerificationStatus,
)


def get_eligible_interpreters(
    specialisation_required=None,
    allow_fallback=False,
    requested_at=None,
    now=None,
):
    """
    Return approved, active interpreters who are available at the requested
    time and match the requested specialisation when one is specified.

    If an interpreter has saved availability periods, those periods determine
    availability. Otherwise, use the weekly schedule and date exceptions.

    If allow_fallback=True, return other eligible interpreters when no
    matching specialist is available.
    """
    requested_time = requested_at or now or timezone.localtime()

    switched_on = InterpreterProfile.objects.filter(
        verification_status=VerificationStatus.APPROVED,
        user__is_active=True,
        is_available=True,
    ).select_related("user")

    profiles = list(switched_on)
    profile_ids = [profile.pk for profile in profiles]

    # Identify interpreters who have explicitly saved availability periods.
    profiles_with_periods = set(
        InterpreterAvailability.objects.filter(
            interpreter_id__in=profile_ids,
        ).values_list("interpreter_id", flat=True).distinct()
    )

    # Period start is inclusive; period end is exclusive.
    available_in_period = set(
        InterpreterAvailability.objects.filter(
            interpreter_id__in=profile_ids,
            start_at__lte=requested_time,
            end_at__gt=requested_time,
        ).values_list("interpreter_id", flat=True).distinct()
    )

    eligible_ids = set(available_in_period)

    # Use the weekly schedule/date exceptions only when no saved periods exist.
    for profile in profiles:
        if profile.pk not in profiles_with_periods:
            if is_available_now(profile, requested_time):
                eligible_ids.add(profile.pk)

    eligible_queryset = switched_on.filter(pk__in=eligible_ids)

    if not specialisation_required:
        return eligible_queryset

    specialists = eligible_queryset.filter(
        specialisation=specialisation_required
    )

    if specialists.exists() or not allow_fallback:
        return specialists

    return eligible_queryset
