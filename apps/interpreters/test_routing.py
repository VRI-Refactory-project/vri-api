from datetime import UTC, datetime, timedelta
from itertools import count

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import (
    InterpreterAvailability,
    InterpreterProfile,
    Specialisation,
    VerificationStatus,
)
from .routing import pick_interpreter

User = get_user_model()

NOW = datetime(2027, 1, 10, 9, 30, tzinfo=UTC)
_numbers = count(1)


def make_interpreter(
    specialisation=Specialisation.GENERAL, *, free=True, switched_on=True
):
    """An approved interpreter. ``free`` gives them a saved period that covers NOW."""
    n = next(_numbers)
    user = User.objects.create_user(
        f"07721{n:05d}",
        "Sign-Connect-2026",
        full_name=f"Interpreter {n}",
        email=f"routing{n}@example.com",
        role=User.Role.INTERPRETER,
    )
    profile = InterpreterProfile.objects.create(
        user=user,
        verification_status=VerificationStatus.APPROVED,
        is_available=switched_on,
        specialisation=specialisation,
    )
    if free:
        InterpreterAvailability.objects.create(
            interpreter=profile,
            start_at=NOW - timedelta(hours=1),
            end_at=NOW + timedelta(hours=1),
        )
    return profile


class PickInterpreterTests(TestCase):
    def test_nobody_available_returns_none(self):
        make_interpreter(free=False)

        self.assertIsNone(pick_interpreter(Specialisation.GENERAL, now=NOW))

    def test_no_context_means_any_eligible_interpreter(self):
        profile = make_interpreter(Specialisation.MEDICAL)

        match = pick_interpreter(None, now=NOW)

        self.assertEqual(match.interpreter, profile)
        self.assertFalse(match.matched_by_fallback)

    def test_specialist_is_preferred(self):
        make_interpreter(Specialisation.GENERAL)
        medical = make_interpreter(Specialisation.MEDICAL)

        match = pick_interpreter(Specialisation.MEDICAL, now=NOW)

        self.assertEqual(match.interpreter, medical)
        self.assertFalse(match.matched_by_fallback)

    def test_falls_back_to_general_when_no_specialist_is_free(self):
        general = make_interpreter(Specialisation.GENERAL)
        make_interpreter(Specialisation.MEDICAL, free=False)

        match = pick_interpreter(Specialisation.MEDICAL, now=NOW)

        self.assertEqual(match.interpreter, general)
        self.assertTrue(match.matched_by_fallback)

    def test_a_specialist_who_declined_does_not_block_the_fallback(self):
        medical = make_interpreter(Specialisation.MEDICAL)
        general = make_interpreter(Specialisation.GENERAL)

        match = pick_interpreter(Specialisation.MEDICAL, exclude=[medical.pk], now=NOW)

        self.assertEqual(match.interpreter, general)
        self.assertTrue(match.matched_by_fallback)

    def test_returns_none_when_everyone_is_excluded(self):
        only = make_interpreter()

        self.assertIsNone(pick_interpreter(None, exclude=[only.pk], now=NOW))

    def test_never_picks_someone_switched_off_or_not_approved(self):
        make_interpreter(switched_on=False)
        pending = make_interpreter()
        pending.verification_status = VerificationStatus.INACTIVE
        pending.is_available = False
        pending.save()

        self.assertIsNone(pick_interpreter(None, now=NOW))

    def test_never_offered_goes_before_offered(self):
        recently = make_interpreter()
        never = make_interpreter()

        match = pick_interpreter(None, last_offered={recently.pk: NOW}, now=NOW)

        self.assertEqual(match.interpreter, never)

    def test_least_recently_offered_goes_first(self):
        earlier = make_interpreter()
        later = make_interpreter()
        last_offered = {
            earlier.pk: NOW - timedelta(hours=3),
            later.pk: NOW - timedelta(minutes=5),
        }

        match = pick_interpreter(None, last_offered=last_offered, now=NOW)

        self.assertEqual(match.interpreter, earlier)

    def test_ties_go_to_the_earliest_profile(self):
        first = make_interpreter()
        make_interpreter()

        match = pick_interpreter(None, now=NOW)

        self.assertEqual(match.interpreter, first)

    def test_an_unknown_context_falls_back_to_anyone(self):
        general = make_interpreter(Specialisation.GENERAL)

        match = pick_interpreter("EDUCATION", now=NOW)

        self.assertEqual(match.interpreter, general)
        self.assertTrue(match.matched_by_fallback)
