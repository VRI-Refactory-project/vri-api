"""Email verification: people must prove they own the email they register with."""

import re

from django.core import mail
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.institutions.models import (
    InstitutionProfile,
    InstitutionStatus,
    OrganisationType,
)
from apps.interpreters.models import InterpreterProfile, VerificationStatus

from .models import RecoveryCode, User

PASSWORD = "Sign-Connect-2026"


def code_from_last_email():
    return re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)


class EmailVerificationTests(APITestCase):
    def setUp(self):
        cache.clear()
        res = self.client.post(
            reverse("auth-register-interpreter"),
            {"full_name": "Ruth Auma", "phone_number": "0772000111", "email": "ruth@example.com",
             "password": PASSWORD},
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.user = User.objects.get(email="ruth@example.com")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")

    def verify(self, code):
        return self.client.post(reverse("auth-email-verify"), {"code": code}, format="json")

    def test_registering_with_an_email_sends_a_code_and_starts_unverified(self):
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["ruth@example.com"])
        self.assertFalse(self.user.email_verified)
        me = self.client.get(reverse("auth-me"))
        self.assertIs(me.data["email_verified"], False)

    def test_registering_without_an_email_sends_nothing(self):
        mail.outbox.clear()
        self.client.post(reverse("auth-register-deaf"),
                         {"full_name": "Amina", "phone_number": "0772123456", "password": PASSWORD}, format="json")
        self.assertEqual(len(mail.outbox), 0)

    def test_correct_code_verifies_the_email(self):
        res = self.verify(code_from_last_email())
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIs(res.data["email_verified"], True)
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.email_verified_at)

    def test_wrong_code_is_rejected(self):
        res = self.verify("000000")
        self.assertEqual(res.status_code, 400)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.email_verified_at)

    def test_a_code_works_only_once(self):
        code = code_from_last_email()
        self.assertEqual(self.verify(code).status_code, 200)
        self.user.email_verified_at = None
        self.user.save()
        self.assertEqual(self.verify(code).status_code, 400)

    def test_code_for_another_purpose_does_not_verify(self):
        self.client.post(reverse("auth-recovery-request"), {"identifier": "ruth@example.com"}, format="json")
        recovery_code = code_from_last_email()
        self.assertEqual(self.verify(recovery_code).status_code, 400)

    def test_five_wrong_codes_burn_the_code(self):
        good = code_from_last_email()
        for _ in range(RecoveryCode.MAX_ATTEMPTS):
            self.verify("000000" if good != "000000" else "111111")
        self.assertEqual(self.verify(good).status_code, 400)

    def test_resend_sends_a_new_code_after_the_cooldown(self):
        RecoveryCode.objects.update(created_at=RecoveryCode.objects.first().created_at.replace(year=2020))
        res = self.client.post(reverse("auth-email-resend"))
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(len(mail.outbox), 2)

    def test_resend_is_refused_straight_after_a_code_was_sent(self):
        res = self.client.post(reverse("auth-email-resend"))
        self.assertEqual(res.status_code, 400)
        self.assertEqual(len(mail.outbox), 1)

    def test_resend_is_refused_once_verified(self):
        self.verify(code_from_last_email())
        self.assertEqual(self.client.post(reverse("auth-email-resend")).status_code, 400)

    def test_endpoints_need_sign_in(self):
        self.client.credentials()
        self.assertEqual(self.verify("123456").status_code, 401)
        self.assertEqual(self.client.post(reverse("auth-email-resend")).status_code, 401)

    def test_failed_email_does_not_break_registration(self):
        from unittest import mock

        with mock.patch("apps.accounts.recovery.send_mail", side_effect=OSError("smtp down")):
            res = self.client.post(
                reverse("auth-register-interpreter"),
                {"full_name": "Sam", "phone_number": "0772000222", "email": "sam@example.com",
                 "password": PASSWORD},
                format="json",
            )
        self.assertEqual(res.status_code, 201)

    def test_changing_the_email_in_admin_clears_verification(self):
        from django.contrib.admin.sites import site
        from django.test import RequestFactory

        self.user.email_verified_at = self.user.date_joined
        self.user.save()
        admin = site._registry[User]

        class FakeForm:
            changed_data = ["email"]

        self.user.email = "new@example.com"
        admin.save_model(RequestFactory().post("/"), self.user, FakeForm(), change=True)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.email_verified_at)


class ApprovalNeedsVerifiedEmailTests(APITestCase):
    def test_interpreter_cannot_be_approved_with_an_unverified_email(self):
        user = User.objects.create_user("0772000111", PASSWORD, full_name="Ruth", email="r@example.com",
                                        role=User.Role.INTERPRETER)
        profile = InterpreterProfile.objects.create(user=user)
        profile.verification_status = VerificationStatus.APPROVED
        with self.assertRaises(ValidationError):
            profile.full_clean()

    def test_interpreter_can_be_approved_once_the_email_is_verified(self):
        user = User.objects.create_user("0772000111", PASSWORD, full_name="Ruth", email="r@example.com",
                                        role=User.Role.INTERPRETER)
        user.email_verified_at = user.date_joined
        user.save()
        profile = InterpreterProfile.objects.create(user=user, verification_status=VerificationStatus.APPROVED)
        profile.full_clean()

    def test_institution_cannot_be_approved_with_an_unverified_email(self):
        user = User.objects.create_user("0772000222", PASSWORD, full_name="Admin", email="i@example.com",
                                        role=User.Role.INSTITUTION)
        profile = InstitutionProfile(user=user, organisation_name="Clinic",
                                     organisation_type=OrganisationType.HOSPITAL, status=InstitutionStatus.APPROVED)
        with self.assertRaises(ValidationError):
            profile.full_clean()

    def test_pending_profiles_are_not_affected(self):
        user = User.objects.create_user("0772000333", PASSWORD, full_name="Ruth", email="x@example.com",
                                        role=User.Role.INTERPRETER)
        InterpreterProfile.objects.create(user=user).full_clean()
