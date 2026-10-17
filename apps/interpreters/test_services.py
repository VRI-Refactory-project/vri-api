from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import (
    InterpreterAvailability,
    InterpreterProfile,
    Specialisation,
    VerificationStatus,
)
from .services import get_eligible_interpreters

User = get_user_model()


class EligibleInterpreterRoutingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            "0772123460",
            "Sign-Connect-2026",
            full_name="Scheduled Interpreter",
            email="scheduled@example.com",
            role=User.Role.INTERPRETER,
        )
        self.profile = InterpreterProfile.objects.create(
            user=self.user,
            verification_status=VerificationStatus.APPROVED,
            is_available=True,
            specialisation=Specialisation.MEDICAL,
        )

        self.requested_at = datetime(2027, 1, 10, 9, 30, tzinfo=UTC)

        InterpreterAvailability.objects.create(
            interpreter=self.profile,
            start_at=datetime(2027, 1, 10, 9, 0, tzinfo=UTC),
            end_at=datetime(2027, 1, 10, 10, 0, tzinfo=UTC),
        )

    def test_includes_interpreter_during_saved_period(self):
        eligible = get_eligible_interpreters(requested_at=self.requested_at)

        self.assertIn(self.profile, eligible)

    def test_excludes_interpreter_outside_saved_period(self):
        outside_period = datetime(2027, 1, 10, 10, 30, tzinfo=UTC)

        eligible = get_eligible_interpreters(requested_at=outside_period)

        self.assertNotIn(self.profile, eligible)

    def test_excludes_interpreter_at_period_end(self):
        period_end = datetime(2027, 1, 10, 10, 0, tzinfo=UTC)

        eligible = get_eligible_interpreters(requested_at=period_end)

        self.assertNotIn(self.profile, eligible)

    def test_excludes_unapproved_interpreter(self):
        self.profile.verification_status = VerificationStatus.INACTIVE
        self.profile.is_available = False
        self.profile.save()

        eligible = get_eligible_interpreters(requested_at=self.requested_at)

        self.assertNotIn(self.profile, eligible)

    def test_excludes_inactive_user(self):
        self.user.is_active = False
        self.user.save()

        eligible = get_eligible_interpreters(requested_at=self.requested_at)

        self.assertNotIn(self.profile, eligible)

    def test_respects_specialisation(self):
        eligible = get_eligible_interpreters(
            specialisation_required=Specialisation.LEGAL,
            requested_at=self.requested_at,
        )

        self.assertNotIn(self.profile, eligible)

    def test_fallback_returns_other_scheduled_specialists(self):
        general_user = User.objects.create_user(
            "0772123461",
            "Sign-Connect-2026",
            full_name="General Interpreter",
            email="general@example.com",
            role=User.Role.INTERPRETER,
        )
        general_profile = InterpreterProfile.objects.create(
            user=general_user,
            verification_status=VerificationStatus.APPROVED,
            is_available=True,
            specialisation=Specialisation.GENERAL,
        )
        InterpreterAvailability.objects.create(
            interpreter=general_profile,
            start_at=datetime(2027, 1, 10, 9, 0, tzinfo=UTC),
            end_at=datetime(2027, 1, 10, 10, 0, tzinfo=UTC),
        )

        eligible = get_eligible_interpreters(
            specialisation_required=Specialisation.LEGAL,
            allow_fallback=True,
            requested_at=self.requested_at,
        )

        self.assertIn(general_profile, eligible)
        self.assertIn(self.profile, eligible)
