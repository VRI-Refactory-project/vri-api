from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from apps.institutions.models import InstitutionProfile
from apps.interpreters.models import InterpreterProfile, VerificationStatus

User = get_user_model()
PASSWORD = "Sign-Connect-2026"


def make_user(role, phone, full_name="Test User", **extra):
    return User.objects.create_user(
        phone, PASSWORD, full_name=full_name, role=role, **extra
    )


class DashboardAccessTests(TestCase):
    """Each dashboard: 401 when signed out, 403 for other roles, 200 for its own role."""

    def setUp(self):
        self.client = APIClient()
        self.users = {
            role: make_user(role, f"07000001{i}0")
            for i, role in enumerate(User.Role.values)
        }

    def get_as(self, url, role=None):
        self.client.force_authenticate(user=self.users[role] if role else None)
        return self.client.get(url)

    def test_deaf_dashboard_is_for_deaf_users_only(self):
        url = "/api/v1/dashboard/deaf/"
        self.assertEqual(self.get_as(url).status_code, 401)
        for role in User.Role.values:
            expected = 200 if role == User.Role.DEAF_HOH else 403
            self.assertEqual(self.get_as(url, role).status_code, expected, role)

    def test_admin_dashboard_is_for_admins_only(self):
        url = "/api/v1/dashboard/admin/"
        self.assertEqual(self.get_as(url).status_code, 401)
        for role in User.Role.values:
            expected = 200 if role == User.Role.ADMIN else 403
            self.assertEqual(self.get_as(url, role).status_code, expected, role)


class DeafDashboardTests(TestCase):
    def test_returns_the_user_and_empty_lists_for_now(self):
        user = make_user(User.Role.DEAF_HOH, "0700000200", full_name="Amina")
        client = APIClient()
        client.force_authenticate(user=user)
        data = client.get("/api/v1/dashboard/deaf/").json()
        self.assertEqual(data["user"]["full_name"], "Amina")
        self.assertEqual(data["user"]["role"], "deaf_hoh")
        self.assertEqual(data["requests"], [])
        self.assertEqual(data["sessions"], [])
        self.assertEqual(data["chats"], [])


class AdminDashboardTests(TestCase):
    def setUp(self):
        self.admin = make_user(User.Role.ADMIN, "0700000300")
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

    def test_counts_are_zero_when_nothing_is_waiting(self):
        data = self.client.get("/api/v1/dashboard/admin/").json()
        self.assertEqual(data["interpreters_pending_verification"], 0)
        self.assertEqual(data["institutions_pending_approval"], 0)
        self.assertEqual(data["users_by_role"]["admin"], 1)
        self.assertEqual(data["users_by_role"]["interpreter"], 0)

    def test_counts_interpreters_and_institutions_waiting_for_review(self):
        pending = make_user(User.Role.INTERPRETER, "0700000310")
        InterpreterProfile.objects.create(user=pending)
        approved = make_user(User.Role.INTERPRETER, "0700000311")
        InterpreterProfile.objects.create(
            user=approved, verification_status=VerificationStatus.APPROVED
        )
        institution = make_user(User.Role.INSTITUTION, "0700000320")
        InstitutionProfile.objects.create(
            user=institution, organisation_name="Mulago", organisation_type="HOSPITAL"
        )

        data = self.client.get("/api/v1/dashboard/admin/").json()
        self.assertEqual(data["interpreters_pending_verification"], 1)
        self.assertEqual(data["institutions_pending_approval"], 1)
        self.assertEqual(data["users_by_role"]["interpreter"], 2)
        self.assertEqual(data["users_by_role"]["institution"], 1)

    def test_deactivated_users_are_not_counted(self):
        make_user(User.Role.DEAF_HOH, "0700000330", is_active=False)
        data = self.client.get("/api/v1/dashboard/admin/").json()
        self.assertEqual(data["users_by_role"]["deaf_hoh"], 0)


class SignInRecordedTests(TestCase):
    def setUp(self):
        cache.clear()  # reset the login rate limit

    def test_signing_in_records_the_time(self):
        admin = make_user(User.Role.ADMIN, "0700000400")
        self.assertIsNone(admin.last_login)
        response = APIClient().post(
            "/api/v1/auth/login/",
            {"identifier": "0700000400", "password": PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        admin.refresh_from_db()
        self.assertIsNotNone(admin.last_login)

    def test_a_failed_sign_in_records_nothing(self):
        admin = make_user(User.Role.ADMIN, "0700000401")
        response = APIClient().post(
            "/api/v1/auth/login/",
            {"identifier": "0700000401", "password": "wrong-password"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        admin.refresh_from_db()
        self.assertIsNone(admin.last_login)


class InterpreterDashboardTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.url = "/api/v1/dashboard/interpreter/"

        self.interpreter = make_user(
            User.Role.INTERPRETER,
            "0700000500",
        )
        self.profile = InterpreterProfile.objects.create(
            user=self.interpreter,
            verification_status=VerificationStatus.APPROVED,
        )

    def test_approved_active_interpreter_can_access_dashboard(self):
        self.client.force_authenticate(user=self.interpreter)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["user"]["role"], User.Role.INTERPRETER)
        self.assertEqual(
            response.data["verification_status"],
            VerificationStatus.APPROVED,
        )
        self.assertIn("is_available", response.data)
        self.assertIn("working_hours_schedule", response.data)

    def test_unauthenticated_user_cannot_access_dashboard(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 401)

    def test_other_roles_cannot_access_interpreter_dashboard(self):
        for index, role in enumerate(
            [User.Role.DEAF_HOH, User.Role.INSTITUTION, User.Role.ADMIN]
        ):
            user = make_user(role, f"070000051{index}0")
            self.client.force_authenticate(user=user)

            response = self.client.get(self.url)

            self.assertEqual(response.status_code, 403, role)

    def test_unapproved_interpreter_cannot_access_dashboard(self):
        self.profile.verification_status = VerificationStatus.INACTIVE
        self.profile.save()

        self.client.force_authenticate(user=self.interpreter)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)

    def test_inactive_account_cannot_access_dashboard(self):
        self.interpreter.is_active = False
        self.interpreter.save()

        self.client.force_authenticate(user=self.interpreter)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)

    def test_interpreter_without_profile_cannot_access_dashboard(self):
        self.profile.delete()

        self.client.force_authenticate(user=self.interpreter)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)
