"""Tests for V2-008 account recovery (see docs/design/V2-008-account-recovery.md)"""

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


class RecoveryTestCase(APITestCase):
    def setUp(self):
        cache.clear()  # reset rate limits
        self.sms_patcher = mock.patch("apps.accounts.views.send_sms")
        self.mock_send_sms = self.sms_patcher.start()
        self.addCleanup(self.sms_patcher.stop)

        self.user = User.objects.create_user(
            "0772123456",
            OLD,
            full_name="Sarah N",
            email="sarah@example.com",
            role=User.Role.INTERPRETER,
        )

    def get_code(self):
        if self.mock_send_sms.called:
            args = self.mock_send_sms.call_args[0]
            message = args[1]
            self.mock_send_sms.reset_mock()
            return re.search(r"\b(\d{6})\b", message).group(1)
        elif mail.outbox:
            return re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)
        raise ValueError("No code was sent")

    def request_code(self, identifier="0772123456"):
        return self.client.post(
            reverse("auth-recovery-request"), {"identifier": identifier}, format="json"
        )

    def confirm(self, code, password=NEW, identifier="0772123456"):
        return self.client.post(
            reverse("auth-recovery-confirm"),
            {"identifier": identifier, "code": code, "new_password": password},
            format="json",
        )

    def login(self, password, identifier="0772123456"):
        self.client.credentials()
        self.client.cookies.pop("active_platform_user", None)
        return self.client.post(
            reverse("auth-login"),
            {"identifier": identifier, "password": password},
            format="json",
        )


class RequestTests(RecoveryTestCase):
    def test_existing_user_gets_one_code_by_email(self):
        response = self.request_code("sarah@example.com")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["sarah@example.com"])
        self.assertEqual(RecoveryCode.objects.filter(user=self.user).count(), 1)

    def test_code_is_stored_hashed(self):
        self.request_code()
        code = self.get_code()
        self.assertNotIn(code, RecoveryCode.objects.get().code_hash)

    def test_email_identifier_also_works(self):
        self.request_code("SARAH@example.com")
        self.assertEqual(len(mail.outbox), 1)

    def test_responses_do_not_reveal_whether_account_exists(self):
        phone_only = User.objects.create_user(
            "0701111222", OLD, full_name="No Email", role=User.Role.DEAF_HOH
        )
        inactive = User.objects.create_user(
            "0703333444",
            OLD,
            full_name="Gone",
            email="gone@example.com",
            role=User.Role.DEAF_HOH,
            is_active=False,
        )
        existing = self.request_code().data
        for identifier in (
            "0799999999",
            "nobody@example.com",
            str(phone_only.phone_number),
            str(inactive.phone_number),
            "not a phone",
        ):
            cache.clear()
            response = self.request_code(identifier)
            self.assertEqual(response.status_code, 200, identifier)
            self.assertEqual(response.data, existing, identifier)

        self.assertEqual(self.mock_send_sms.call_count, 2)

    def test_second_request_within_a_minute_sends_nothing(self):
        phone_user = User.objects.create_user(
            "0701111222", OLD, full_name="Phone Only", role=User.Role.DEAF_HOH
        )
        self.request_code(str(phone_user.phone_number))
        self.request_code(str(phone_user.phone_number))
        self.assertEqual(self.mock_send_sms.call_count, 1)


class ConfirmTests(RecoveryTestCase):
    def get_code(self):
        self.request_code()
        return super().get_code()

    def test_right_code_resets_password_and_signs_in(self):
        response = self.confirm(self.get_code())
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertEqual(self.login(NEW).status_code, 200)
        self.assertEqual(self.login(OLD).status_code, 400)

    def test_role_profile_and_status_unchanged(self):
        before = User.objects.values(
            "role", "is_active", "full_name", "email", "phone_number"
        ).get(pk=self.user.pk)
        self.confirm(self.get_code())
        after = User.objects.values(
            "role", "is_active", "full_name", "email", "phone_number"
        ).get(pk=self.user.pk)
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
        self.assertEqual(
            self.confirm(code, password="Another-Pass-2026").status_code, 400
        )

    def test_expired_code_rejected(self):
        code = self.get_code()
        RecoveryCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.confirm(code).status_code, 400)

    def test_older_code_stops_working_after_a_new_one(self):
        first = self.get_code()
        RecoveryCode.objects.update(
            created_at=timezone.now() - timedelta(minutes=2)
        )  # skip the cooldown
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
        response = self.client.post(
            reverse("auth-token-refresh"), {"refresh": refresh}, format="json"
        )
        self.assertEqual(response.status_code, 401)


class VerifyTests(RecoveryTestCase):
    def get_code(self):
        self.request_code()
        return super().get_code()

    def verify(self, code, identifier="0772123456"):
        return self.client.post(
            reverse("auth-recovery-verify"),
            {"identifier": identifier, "code": code},
            format="json",
        )

    def test_right_code_is_valid(self):
        response = self.verify(self.get_code())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["detail"], "Code is valid.")

    def test_verify_does_not_use_the_code_up(self):
        code = self.get_code()
        self.verify(code)
        self.assertEqual(self.confirm(code).status_code, 200)

    def test_verify_does_not_change_password(self):
        self.verify(self.get_code())
        self.assertEqual(self.login(NEW).status_code, 400)
        self.assertEqual(self.login(OLD).status_code, 200)

    def test_wrong_code_is_rejected_with_the_same_error_as_confirm(self):
        code = self.get_code()
        wrong = self.verify("000000" if code != "000000" else "111111")
        self.assertEqual(wrong.status_code, 400)
        self.assertEqual(list(wrong.data), ["code"])

    def test_unknown_account_is_rejected(self):
        code = self.get_code()
        self.assertEqual(self.verify(code, identifier="0799999999").status_code, 400)

    def test_deactivated_account_is_rejected(self):
        code = self.get_code()
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        self.assertEqual(self.verify(code).status_code, 400)

    def test_expired_code_is_rejected(self):
        code = self.get_code()
        RecoveryCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.verify(code).status_code, 400)

    def test_used_code_is_rejected(self):
        code = self.get_code()
        self.confirm(code)
        self.assertEqual(self.verify(code).status_code, 400)

    def test_wrong_guesses_count_towards_the_same_limit(self):
        code = self.get_code()
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(4):
            self.verify(wrong)
        self.assertEqual(self.confirm(code).status_code, 400)


class PasswordChangeTests(RecoveryTestCase):
    def test_change_password_clears_must_change_flag(self):
        User.objects.filter(pk=self.user.pk).update(must_change_password=True)
        access = self.login(OLD).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        response = self.client.post(
            reverse("auth-password-change"),
            {"current_password": OLD, "new_password": NEW},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["user"]["must_change_password"])
        self.client.credentials()
        self.assertEqual(self.login(NEW).status_code, 200)

    def test_wrong_current_password_rejected(self):
        access = self.login(OLD).data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        response = self.client.post(
            reverse("auth-password-change"),
            {"current_password": "wrong-one", "new_password": NEW},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("current_password", response.data)


class AdminResetTests(RecoveryTestCase):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user(
            "0700000001",
            "Admin-Pass-2026",
            full_name="Admin A",
            email="admin@example.com",
            role=User.Role.ADMIN,
        )
        self.target = User.objects.create_user(
            "0701111222", OLD, full_name="Phone Only", role=User.Role.DEAF_HOH
        )
        access = self.login("Admin-Pass-2026", identifier="0700000001").data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    def admin_request(self, pk=None):
        return self.client.post(
            reverse("manage-reset-password-request", args=[pk or self.target.pk])
        )

    def admin_confirm(self, code, temp="Temp-Pass-4821", pk=None):
        return self.client.post(
            reverse("manage-reset-password-confirm", args=[pk or self.target.pk]),
            {"code": code, "temporary_password": temp},
            format="json",
        )

    def test_code_goes_to_admins_own_email(self):
        response = self.admin_request()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mail.outbox[-1].to, ["admin@example.com"])
        self.assertIn("Phone Only", mail.outbox[-1].body)
        self.assertIn("ad***@example.com", response.data["detail"])

    def test_full_reset_sets_temporary_password_and_flag(self):
        self.admin_request()
        code = re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)
        response = self.admin_confirm(code)
        self.assertEqual(response.status_code, 200)
        self.client.credentials()
        login = self.login("Temp-Pass-4821", identifier="0701111222")
        self.assertEqual(login.status_code, 200)
        self.assertTrue(login.data["user"]["must_change_password"])
        self.assertEqual(User.objects.get(pk=self.target.pk).role, User.Role.DEAF_HOH)

    def test_wrong_code_rejected(self):
        self.admin_request()
        code = re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)
        self.assertEqual(
            self.admin_confirm("000000" if code != "000000" else "111111").status_code,
            400,
        )

    def test_code_for_one_user_cannot_reset_another(self):
        other = User.objects.create_user(
            "0705555666", OLD, full_name="Other", role=User.Role.DEAF_HOH
        )
        self.admin_request()
        code = re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)
        self.assertEqual(self.admin_confirm(code, pk=other.pk).status_code, 400)

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
        other_admin = User.objects.create_user(
            "0700000002", OLD, full_name="Admin B", role=User.Role.ADMIN
        )
        self.assertEqual(self.admin_request(pk=other_admin.pk).status_code, 403)

    def test_admin_cannot_reset_self_this_way(self):
        self.assertEqual(self.admin_request(pk=self.admin.pk).status_code, 400)


class HardeningTests(RecoveryTestCase):
    def backdate_codes(self, minutes=2):
        RecoveryCode.objects.update(
            created_at=timezone.now() - timedelta(minutes=minutes)
        )
        cache.clear()

    def test_sms_failure_keeps_response_neutral_and_leaves_no_code(self):
        self.mock_send_sms.side_effect = Exception("sms down")
        response = self.request_code()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, self.request_code("0799999999").data)
        self.assertFalse(RecoveryCode.objects.exists())

    def test_hourly_code_limit_per_account(self):
        phone_user = User.objects.create_user(
            "0701111222", OLD, full_name="Phone Only", role=User.Role.DEAF_HOH
        )
        for _ in range(5):
            self.request_code(str(phone_user.phone_number))
            self.backdate_codes()
        self.request_code(str(phone_user.phone_number))
        self.assertEqual(self.mock_send_sms.call_count, 5)

    def test_account_locked_after_too_many_wrong_codes(self):
        phone_user = User.objects.create_user(
            "0701111222", OLD, full_name="Phone Only", role=User.Role.DEAF_HOH
        )
        for _ in range(3):
            self.request_code(str(phone_user.phone_number))
            code = self.get_code()
            wrong = "000000" if code != "000000" else "111111"
            for _ in range(5):
                cache.clear()
                self.confirm(wrong, identifier=str(phone_user.phone_number))
            self.backdate_codes()

        self.mock_send_sms.reset_mock()
        self.request_code(str(phone_user.phone_number))
        self.assertEqual(self.mock_send_sms.call_count, 0)

    def test_code_cannot_be_claimed_twice(self):
        from . import recovery

        self.request_code()
        rc = RecoveryCode.objects.get()
        self.assertTrue(recovery.mark_used(rc))
        self.assertFalse(recovery.mark_used(rc))


class MustChangePasswordTests(RecoveryTestCase):
    def test_server_blocks_everything_but_password_change(self):
        admin = User.objects.create_user(
            "0700000009",
            OLD,
            full_name="Admin Z",
            email="z@example.com",
            role=User.Role.ADMIN,
        )
        access = self.login(OLD, identifier="0700000009").data["access"]
        User.objects.filter(pk=admin.pk).update(must_change_password=True)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        blocked = self.client.post(
            reverse("manage-reset-password-request", args=[self.user.pk])
        )
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(blocked.data["code"], "password_change_required")
        self.assertEqual(self.client.get(reverse("auth-me")).status_code, 200)
        changed = self.client.post(
            reverse("auth-password-change"),
            {"current_password": OLD, "new_password": NEW},
            format="json",
        )
        self.assertEqual(changed.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {changed.data['access']}")
        allowed = self.client.post(
            reverse("manage-reset-password-request", args=[self.user.pk])
        )
        self.assertEqual(allowed.status_code, 200)
