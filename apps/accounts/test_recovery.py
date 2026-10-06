"""Tests for V2-008 account recovery (see docs/design/V2-008-account-recovery.md)."""

import re
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from .models import RecoveryCode

User = get_user_model()
OLD = "Old-Pass-2026"
NEW = "New-Pass-2026"


def code_from_last_email():
    return re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)


class RecoveryTestCase(APITestCase):
    def setUp(self):
        cache.clear()  # reset rate limits
        self.user = User.objects.create_user(
            "0772123456", OLD, full_name="Sarah N", email="sarah@example.com", role=User.Role.INTERPRETER
        )

    def request_code(self, identifier="0772123456"):
        return self.client.post(reverse("auth-recovery-request"), {"identifier": identifier}, format="json")

    def confirm(self, code, password=NEW, identifier="0772123456"):
        return self.client.post(reverse("auth-recovery-confirm"),
                                {"identifier": identifier, "code": code, "new_password": password}, format="json")

    def login(self, password, identifier="0772123456"):
        return self.client.post(reverse("auth-login"), {"identifier": identifier, "password": password},
                                format="json")


class RequestTests(RecoveryTestCase):
    def test_existing_user_gets_one_code_by_email(self):
        response = self.request_code()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["sarah@example.com"])
        self.assertEqual(RecoveryCode.objects.filter(user=self.user).count(), 1)

    def test_code_is_stored_hashed(self):
        self.request_code()
        code = code_from_last_email()
        self.assertNotIn(code, RecoveryCode.objects.get().code_hash)

    def test_email_identifier_also_works(self):
        self.request_code("SARAH@example.com")
        self.assertEqual(len(mail.outbox), 1)

    def test_responses_do_not_reveal_whether_account_exists(self):
        phone_only = User.objects.create_user("0701111222", OLD, full_name="No Email", role=User.Role.DEAF_HOH)
        inactive = User.objects.create_user("0703333444", OLD, full_name="Gone", email="gone@example.com",
                                            role=User.Role.DEAF_HOH, is_active=False)
        existing = self.request_code().data
        for identifier in ("0799999999", "nobody@example.com", str(phone_only.phone_number),
                           str(inactive.phone_number), "not a phone"):
            cache.clear()
            response = self.request_code(identifier)
            self.assertEqual(response.status_code, 200, identifier)
            self.assertEqual(response.data, existing, identifier)
        self.assertEqual(len(mail.outbox), 1)  # only the real, active user with email got a code

    def test_second_request_within_a_minute_sends_nothing(self):
        self.request_code()
        self.request_code()
        self.assertEqual(len(mail.outbox), 1)

    def test_request_is_rate_limited(self):
        for _ in range(5):
            self.request_code("0799999999")
        self.assertEqual(self.request_code("0799999999").status_code, 429)


class ConfirmTests(RecoveryTestCase):
    def get_code(self):
        self.request_code()
        return code_from_last_email()

    def test_right_code_resets_password_and_signs_in(self):
        response = self.confirm(self.get_code())
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertEqual(self.login(NEW).status_code, 200)
        self.assertEqual(self.login(OLD).status_code, 400)

    def test_role_profile_and_status_unchanged(self):
        before = User.objects.values("role", "is_active", "full_name", "email", "phone_number").get(pk=self.user.pk)
        self.confirm(self.get_code())
        after = User.objects.values("role", "is_active", "full_name", "email", "phone_number").get(pk=self.user.pk)
        self.assertEqual(before, after)

    def test_bad_codes_all_give_the_same_error(self):
        code = self.get_code()
        wrong = self.confirm("000000" if code != "000000" else "111111")
        unknown_account = self.confirm(code, identifier="0799999999")
        self.assertEqual(wrong.status_code, 400)
        self.assertEqual(wrong.data, unknown_account.data)
        self.assertEqual(list(wrong.data), ["code"])

    def test_code_cannot_be_used_twice(self):
        code = self.get_code()
        self.assertEqual(self.confirm(code).status_code, 200)
        self.assertEqual(self.confirm(code, password="Another-Pass-2026").status_code, 400)

    def test_expired_code_rejected(self):
        code = self.get_code()
        RecoveryCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.confirm(code).status_code, 400)

    def test_older_code_stops_working_after_a_new_one(self):
        first = self.get_code()
        RecoveryCode.objects.update(created_at=timezone.now() - timedelta(minutes=2))  # skip the cooldown
        cache.clear()
        second = self.get_code()
        if first != second:
            self.assertEqual(self.confirm(first).status_code, 400)
        self.assertEqual(self.confirm(second).status_code, 200)

    def test_five_wrong_tries_kill_the_code(self):
        code = self.get_code()
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(5):
            self.confirm(wrong)
        self.assertEqual(self.confirm(code).status_code, 400)

    def test_weak_password_rejected_and_code_kept(self):
        code = self.get_code()
        response = self.confirm(code, password="123456")
        self.assertEqual(response.status_code, 400)
        self.assertIn("new_password", response.data)
        self.assertEqual(self.confirm(code).status_code, 200)

    def test_deactivated_account_cannot_recover(self):
        code = self.get_code()
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        self.assertEqual(self.confirm(code).status_code, 400)

    def test_old_refresh_tokens_stop_working(self):
        refresh = self.login(OLD).data["refresh"]
        self.confirm(self.get_code())
        response = self.client.post(reverse("auth-token-refresh"), {"refresh": refresh}, format="json")
        self.assertEqual(response.status_code, 401)

    def test_confirm_is_rate_limited(self):
        for _ in range(10):
            self.confirm("000000", identifier="0799999999")
        self.assertEqual(self.confirm("000000", identifier="0799999999").status_code, 429)


class PasswordChangeTests(RecoveryTestCase):
    def test_change_password_clears_must_change_flag(self):
        User.objects.filter(pk=self.user.pk).update(must_change_password=True)
        access = self.login(OLD).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        response = self.client.post(reverse("auth-password-change"),
                                    {"current_password": OLD, "new_password": NEW}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["user"]["must_change_password"])
        self.client.credentials()
        self.assertEqual(self.login(NEW).status_code, 200)

    def test_wrong_current_password_rejected(self):
        access = self.login(OLD).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        response = self.client.post(reverse("auth-password-change"),
                                    {"current_password": "wrong-one", "new_password": NEW}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("current_password", response.data)


class AdminResetTests(RecoveryTestCase):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user("0700000001", "Admin-Pass-2026", full_name="Admin A",
                                              email="admin@example.com", role=User.Role.ADMIN)
        self.target = User.objects.create_user("0701111222", OLD, full_name="Phone Only", role=User.Role.DEAF_HOH)
        access = self.login("Admin-Pass-2026", identifier="0700000001").data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    def admin_request(self, pk=None):
        return self.client.post(reverse("manage-reset-password-request", args=[pk or self.target.pk]))

    def admin_confirm(self, code, temp="Temp-Pass-4821", pk=None):
        return self.client.post(reverse("manage-reset-password-confirm", args=[pk or self.target.pk]),
                                {"code": code, "temporary_password": temp}, format="json")

    def test_code_goes_to_admins_own_email(self):
        response = self.admin_request()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mail.outbox[-1].to, ["admin@example.com"])
        self.assertIn("Phone Only", mail.outbox[-1].body)
        self.assertIn("ad***@example.com", response.data["detail"])

    def test_full_reset_sets_temporary_password_and_flag(self):
        self.admin_request()
        response = self.admin_confirm(code_from_last_email())
        self.assertEqual(response.status_code, 200)
        self.client.credentials()
        login = self.login("Temp-Pass-4821", identifier="0701111222")
        self.assertEqual(login.status_code, 200)
        self.assertTrue(login.data["user"]["must_change_password"])
        self.assertEqual(User.objects.get(pk=self.target.pk).role, User.Role.DEAF_HOH)

    def test_wrong_code_rejected(self):
        self.admin_request()
        code = code_from_last_email()
        self.assertEqual(self.admin_confirm("000000" if code != "000000" else "111111").status_code, 400)

    def test_code_for_one_user_cannot_reset_another(self):
        other = User.objects.create_user("0705555666", OLD, full_name="Other", role=User.Role.DEAF_HOH)
        self.admin_request()
        self.assertEqual(self.admin_confirm(code_from_last_email(), pk=other.pk).status_code, 400)

    def test_non_admin_forbidden(self):
        self.client.credentials()
        access = self.login(OLD).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        self.assertEqual(self.admin_request().status_code, 403)

    def test_not_signed_in_rejected(self):
        self.client.credentials()
        self.assertEqual(self.admin_request().status_code, 401)

    def test_admin_without_email_is_told_to_add_one(self):
        User.objects.filter(pk=self.admin.pk).update(email=None)
        response = self.admin_request()
        self.assertEqual(response.status_code, 400)
        self.assertIn("email", str(response.data))

    def test_regular_admin_cannot_reset_another_admin(self):
        other_admin = User.objects.create_user("0700000002", OLD, full_name="Admin B", role=User.Role.ADMIN)
        self.assertEqual(self.admin_request(pk=other_admin.pk).status_code, 400)

    def test_admin_cannot_reset_self_this_way(self):
        self.assertEqual(self.admin_request(pk=self.admin.pk).status_code, 400)


class EmailFailureTests(RecoveryTestCase):
    def test_email_failure_returns_server_error_not_silent_success(self):
        with mock.patch("apps.accounts.recovery.send_mail", side_effect=OSError("smtp down")):
            with self.assertRaises(OSError):
                self.request_code()
