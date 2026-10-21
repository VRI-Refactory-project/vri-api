"""Pick which interpreter a new request should be offered to (V2-022: BE-01 and BE-02).

This only answers "who is next?". It does not create offers or touch the request, so it can be
built and tested before the request model exists. The caller passes in what only it knows:

* ``exclude``: interpreters who must not be picked, for example ones who already declined this
  request or who currently hold another open offer.
* ``last_offered``: when each interpreter was last offered a request, so the person who has waited
  longest is asked first.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from .models import InterpreterProfile, Specialisation
from .services import get_eligible_interpreters

_NEVER = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class RoutingMatch:
    interpreter: InterpreterProfile
    # True when the person asked for a specialist and none was free, so a general
    # interpreter was chosen instead. Stored on the request as ``matched_by_fallback``.
    matched_by_fallback: bool


def pick_interpreter(context=None, *, exclude=(), last_offered=None, now=None):
    """Return a ``RoutingMatch`` for the next interpreter to offer, or ``None`` if nobody can take it.

    ``None`` means the request should wait in the queue (BE-04).

    * No context, or GENERAL: anyone who is eligible. This is not a fallback.
    * A specialist context (for example MEDICAL): eligible specialists first. If there are none
      left, anyone eligible, and the match is marked as a fallback.

    Interpreters in ``exclude`` are removed before deciding whether a specialist is available, so
    a specialist who declined does not stop the request falling back to a general interpreter.
    """
    last_offered = last_offered or {}
    excluded = set(exclude)

    eligible = get_eligible_interpreters(now=now).exclude(pk__in=excluded)

    wanted = context or Specialisation.GENERAL
    if wanted == Specialisation.GENERAL:
        chosen = _longest_waiting(eligible, last_offered)
        return RoutingMatch(chosen, False) if chosen else None

    # Filtered in Python because the specialisations are a JSON list.
    specialists = [p for p in eligible if p.has_specialisation(wanted)]
    chosen = _longest_waiting(specialists, last_offered)
    if chosen:
        return RoutingMatch(chosen, False)

    chosen = _longest_waiting(eligible, last_offered)
    return RoutingMatch(chosen, True) if chosen else None


def _longest_waiting(profiles, last_offered):
    """The interpreter offered a request least recently. Never offered comes first; ties go to the
    earliest profile so the answer is the same every time."""

    def key(profile):
        offered = last_offered.get(profile.pk)
        return (offered is not None, offered or _NEVER, profile.pk)

    return min(profiles, key=key, default=None)
