from datetime import datetime, time
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import (
    DateException,
    InterpreterProfile,
    Specialisation,
    VerificationStatus,
    WeeklyAvailability,
)
from .availability import unavailable_reason
from .services import get_eligible_interpreters

EAT = ZoneInfo("Africa/Kampala")
MONDAY_10AM = datetime(2026, 10, 12, 10, 0, tzinfo=EAT)  # 12 Oct 2026 is a Monday
MONDAY_8PM = datetime(2026, 10, 12, 20, 0, tzinfo=EAT)
MONDAY_1PM = datetime(2026, 10, 12, 13, 0, tzinfo=EAT)
SATURDAY_10AM = datetime(2026, 10, 17, 10, 0, tzinfo=EAT)


def make_interpreter(specialisation=Specialisation.MEDICAL):
    user = get_user_model().objects.create_user(
        phone_number="+256700000001",
        password="Pass1234!",
        full_name="Test Interpreter",
        role="interpreter",
    )
    profile = InterpreterProfile.objects.create(
        user=user,
        verification_status=VerificationStatus.APPROVED,
        is_available=True,
        specialisation=specialisation,
    )
    # Default week: Monday to Friday, 08:00 to 17:00
    for weekday in range(5):
        WeeklyAvailability.objects.create(
            interpreter=profile,
            weekday=weekday,
            is_available=True,
            start_time=time(8, 0),
            end_time=time(17, 0),
        )
    return profile


class AvailabilityRoutingTests(TestCase):
    def setUp(self):
        self.interpreter = make_interpreter()

    def offered(self, specialisation=Specialisation.MEDICAL, now=MONDAY_10AM):
        return get_eligible_interpreters(
            specialisation_required=specialisation, now=now
        )

    # Criterion 1: marked available, matching specialisation -> offered requests
    def test_available_in_hours_with_matching_specialisation_is_offered(self):
        self.assertIn(self.interpreter, self.offered())

    def test_other_specialisation_is_not_offered(self):
        self.assertNotIn(self.interpreter, self.offered(Specialisation.LEGAL))

    # Criterion 2: busy or offline (unavailable) -> no new requests
    def test_dashboard_switch_off_is_not_offered(self):
        self.interpreter.is_available = False
        self.interpreter.save()
        self.assertNotIn(self.interpreter, self.offered())

    def test_outside_default_hours_is_not_offered(self):
        self.assertNotIn(self.interpreter, self.offered(now=MONDAY_8PM))

    # Criterion 3: the change applies to the very next routing decision
    def test_change_applies_to_next_routing_call(self):
        self.assertIn(self.interpreter, self.offered())

        self.interpreter.is_available = False
        self.interpreter.save()
        self.assertNotIn(self.interpreter, self.offered())  # very next call

        self.interpreter.is_available = True
        self.interpreter.save()
        self.assertIn(self.interpreter, self.offered())

    # Date exceptions beat the default week
    def test_unavailable_all_day_exception_beats_default_week(self):
        DateException.objects.create(
            interpreter=self.interpreter,
            date=MONDAY_10AM.date(),
            is_available=False,
        )
        self.assertNotIn(self.interpreter, self.offered())

    def test_modified_hours_exception_beats_default_week(self):
        DateException.objects.create(
            interpreter=self.interpreter,
            date=MONDAY_10AM.date(),
            is_available=True,
            start_time=time(12, 0),
            end_time=time(14, 0),
        )
        self.assertNotIn(self.interpreter, self.offered(now=MONDAY_10AM))
        self.assertIn(self.interpreter, self.offered(now=MONDAY_1PM))

    # Nothing saved yet -> the built-in default week (Mon-Fri 08:00-17:00) applies
    def test_no_saved_schedule_uses_default_week(self):
        WeeklyAvailability.objects.all().delete()
        self.assertIn(self.interpreter, self.offered(now=MONDAY_10AM))
        self.assertNotIn(self.interpreter, self.offered(now=SATURDAY_10AM))


class UnavailableReasonTests(TestCase):
    """The reason the dashboard shows. None means she can be offered a request."""

    def setUp(self):
        self.interpreter = make_interpreter()

    def test_available_in_hours_has_no_reason(self):
        self.assertIsNone(unavailable_reason(self.interpreter, MONDAY_10AM))

    def test_outside_hours(self):
        self.assertEqual(
            unavailable_reason(self.interpreter, MONDAY_8PM), "outside_hours"
        )

    def test_day_off(self):
        self.assertEqual(unavailable_reason(self.interpreter, SATURDAY_10AM), "day_off")

    def test_switched_off(self):
        self.interpreter.is_available = False
        self.interpreter.save()

        self.assertEqual(
            unavailable_reason(self.interpreter, MONDAY_10AM), "switched_off"
        )

    def test_not_approved(self):
        self.interpreter.is_available = False
        self.interpreter.verification_status = VerificationStatus.INACTIVE
        self.interpreter.save()

        self.assertEqual(
            unavailable_reason(self.interpreter, MONDAY_10AM), "not_approved"
        )
