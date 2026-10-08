from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase

from .models import InterpreterProfile, VerificationDecision, VerificationStatus

User = get_user_model()
PASSWORD = "Sign-Connect-2026"


def make_interpreter(phone="0772123456", status=VerificationStatus.INACTIVE, **profile_fields):
    user = User.objects.create_user(
        phone, PASSWORD, full_name="Test Interpreter", email=f"i{phone}@example.com", role=User.Role.INTERPRETER
    )
    return InterpreterProfile.objects.create(user=user, verification_status=status, **profile_fields)


def make_admin(phone="0700000100"):
    return User.objects.create_superuser(phone, PASSWORD, full_name="Admin User", email=f"a{phone}@example.com")


class VerificationModelTests(TestCase):
    def test_deactivated_interpreter_cannot_be_available(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_interpreter(status=VerificationStatus.DEACTIVATED, is_available=True)

    def test_decisions_are_listed_newest_first(self):
        profile, admin = make_interpreter(), make_admin()
        first = VerificationDecision.objects.create(
            interpreter=profile, admin=admin, action=VerificationDecision.Action.REJECT, reason="Blurry scan",
            previous_status=VerificationStatus.INACTIVE, new_status=VerificationStatus.REJECTED,
        )
        second = VerificationDecision.objects.create(
            interpreter=profile, admin=admin, action=VerificationDecision.Action.APPROVE,
            previous_status=VerificationStatus.REJECTED, new_status=VerificationStatus.APPROVED,
        )
        self.assertEqual(list(profile.decisions.all()), [second, first])

    def test_audit_history_survives_user_deletion_attempts(self):
        profile, admin = make_interpreter(), make_admin()
        VerificationDecision.objects.create(
            interpreter=profile, admin=admin, action=VerificationDecision.Action.APPROVE,
            previous_status=VerificationStatus.INACTIVE, new_status=VerificationStatus.APPROVED,
        )
        with self.assertRaises(ProtectedError):
            admin.delete()
        with self.assertRaises(ProtectedError):
            profile.user.delete()
        self.assertEqual(VerificationDecision.objects.count(), 1)
