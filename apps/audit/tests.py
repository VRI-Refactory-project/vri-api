from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from .models import AuditLog

User = get_user_model()
PASSWORD = "Sign-Connect-2026"
LOGIN = "/api/v1/auth/login/"
ADMIN_DASHBOARD = "/api/v1/dashboard/admin/"


def make_user(role, phone, full_name="Test User"):
    return User.objects.create_user(phone, PASSWORD, full_name=full_name, role=role)


def sign_in(phone, password=PASSWORD, **extra):
    return APIClient().post(
        LOGIN, {"identifier": phone, "password": password}, format="json", **extra
    )


class AdminSignInLogTests(TestCase):
    def setUp(self):
        cache.clear()  # reset the login rate limit

    def test_an_administrator_signing_in_is_logged_with_who_and_where_from(self):
        admin = make_user(User.Role.ADMIN, "0700000500")
        self.assertEqual(sign_in("0700000500", REMOTE_ADDR="10.1.2.3").status_code, 200)
        entry = AuditLog.objects.get()
        self.assertEqual(entry.user, admin)
        self.assertEqual(entry.event, AuditLog.Event.ADMIN_SIGN_IN)
        self.assertEqual(entry.ip_address, "10.1.2.3")

    def test_every_sign_in_adds_an_entry(self):
        make_user(User.Role.ADMIN, "0700000501")
        sign_in("0700000501")
        sign_in("0700000501")
        self.assertEqual(AuditLog.objects.count(), 2)

    def test_other_roles_are_not_logged(self):
        for i, role in enumerate(
            [User.Role.DEAF_HOH, User.Role.INTERPRETER, User.Role.INSTITUTION]
        ):
            make_user(role, f"070000052{i}")
            self.assertEqual(sign_in(f"070000052{i}").status_code, 200)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_a_failed_sign_in_is_not_logged(self):
        make_user(User.Role.ADMIN, "0700000502")
        self.assertEqual(
            sign_in("0700000502", password="wrong-password").status_code, 400
        )
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_the_history_survives_deleting_the_account(self):
        admin = make_user(User.Role.ADMIN, "0700000503")
        sign_in("0700000503")
        admin.delete()
        self.assertIsNone(AuditLog.objects.get().user)


class AdminDashboardAuditTests(TestCase):
    def setUp(self):
        self.admin = make_user(User.Role.ADMIN, "0700000510", full_name="Ada Admin")
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

    def test_shows_nothing_before_any_sign_in(self):
        self.assertEqual(
            self.client.get(ADMIN_DASHBOARD).json()["recent_admin_sign_ins"], []
        )

    def test_lists_the_latest_sign_ins_newest_first_and_at_most_ten(self):
        for _ in range(12):
            AuditLog.objects.create(
                user=self.admin,
                event=AuditLog.Event.ADMIN_SIGN_IN,
                ip_address="10.0.0.9",
            )
        newest = AuditLog.objects.create(
            user=self.admin, event=AuditLog.Event.ADMIN_SIGN_IN, ip_address="10.0.0.1"
        )
        entries = self.client.get(ADMIN_DASHBOARD).json()["recent_admin_sign_ins"]
        self.assertEqual(len(entries), 10)
        self.assertEqual(entries[0]["id"], newest.id)
        self.assertEqual(entries[0]["admin"], "Ada Admin")
        self.assertEqual(entries[0]["ip_address"], "10.0.0.1")
        self.assertIn("at", entries[0])
