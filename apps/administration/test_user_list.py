from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.institutions.models import (
    InstitutionProfile,
    InstitutionStaff,
    InstitutionStatus,
    OrganisationType,
    StaffRole,
)
from apps.interpreters.models import InterpreterProfile, VerificationStatus

User = get_user_model()
PASSWORD = "Sign-Connect-2026"
URL = "/api/v1/administration/users/"


def make_user(role, phone, full_name="Test User", **extra):
    return User.objects.create_user(
        phone, PASSWORD, full_name=full_name, role=role, **extra
    )


def make_interpreter(phone, status, **extra):
    user = make_user(User.Role.INTERPRETER, phone, **extra)
    InterpreterProfile.objects.create(user=user, verification_status=status)
    return user


def make_institution_user(phone, status, revoked=False, **extra):
    user = make_user(User.Role.INSTITUTION, phone, **extra)
    institution = InstitutionProfile.objects.create(
        user=user,
        organisation_name="Mulago Hospital",
        organisation_type=OrganisationType.HOSPITAL,
        status=status,
    )
    InstitutionStaff.objects.create(
        institution=institution,
        user=user,
        role=StaffRole.ADMIN,
        created_by=user,
        revoked_at=timezone.now() if revoked else None,
    )
    return user


class AdminUserListAccessTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_signed_out_is_refused(self):
        self.assertEqual(self.client.get(URL).status_code, 401)

    def test_only_administrators_can_list_users(self):
        for i, role in enumerate(User.Role.values):
            user = make_user(role, f"07000008{i}0")
            self.client.force_authenticate(user=user)
            expected = 200 if role == User.Role.ADMIN else 403
            self.assertEqual(self.client.get(URL).status_code, expected, role)


class AdminUserListTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = make_user(User.Role.ADMIN, "0700000001", "Ada Admin")
        self.client.force_authenticate(user=self.admin)

    def by_name(self, **params):
        response = self.client.get(URL, params)
        self.assertEqual(response.status_code, 200)
        return {row["full_name"]: row for row in response.json()}

    def test_row_has_the_fields_the_page_needs(self):
        make_user(
            User.Role.DEAF_HOH,
            "0700000002",
            "Dan Deaf",
            email="dan@example.com",
        )
        row = self.by_name()["Dan Deaf"]
        self.assertEqual(
            set(row),
            {
                "id",
                "full_name",
                "email",
                "phone_number",
                "role",
                "verification_status",
                "created_at",
                "last_login",
            },
        )
        self.assertEqual(row["email"], "dan@example.com")
        self.assertEqual(row["phone_number"], "+256700000002")
        self.assertEqual(row["role"], "deaf_hoh")
        self.assertIsNone(row["last_login"])

    def test_user_without_email_has_an_empty_string(self):
        make_user(User.Role.DEAF_HOH, "0700000002", "No Email")
        self.assertEqual(self.by_name()["No Email"]["email"], "")

    def test_verification_status_by_role(self):
        make_user(User.Role.DEAF_HOH, "0700000010", "Deaf Active")
        make_user(User.Role.DEAF_HOH, "0700000011", "Deaf Off", is_active=False)
        make_interpreter("0700000012", VerificationStatus.APPROVED, full_name="Int Ok")
        make_interpreter("0700000013", VerificationStatus.INACTIVE, full_name="Int New")
        make_interpreter("0700000014", VerificationStatus.REJECTED, full_name="Int No")
        make_interpreter(
            "0700000015", VerificationStatus.DEACTIVATED, full_name="Int Off"
        )
        make_institution_user(
            "0700000016", InstitutionStatus.APPROVED, full_name="Inst Ok"
        )
        make_institution_user(
            "0700000017", InstitutionStatus.PENDING, full_name="Inst New"
        )
        make_institution_user(
            "0700000018", InstitutionStatus.REJECTED, full_name="Inst No"
        )
        make_institution_user(
            "0700000019", InstitutionStatus.DELETED, full_name="Inst Gone"
        )
        make_institution_user(
            "0700000020",
            InstitutionStatus.APPROVED,
            revoked=True,
            full_name="Inst Revoked",
        )
        rows = self.by_name()
        expected = {
            "Ada Admin": "verified",
            "Deaf Active": "verified",
            "Deaf Off": "suspended",
            "Int Ok": "verified",
            "Int New": "pending",
            "Int No": "rejected",
            "Int Off": "suspended",
            "Inst Ok": "verified",
            "Inst New": "pending",
            "Inst No": "rejected",
            "Inst Gone": "suspended",
            "Inst Revoked": "suspended",
        }
        for name, status in expected.items():
            self.assertEqual(rows[name]["verification_status"], status, name)

    def test_interpreter_without_profile_is_pending(self):
        make_user(User.Role.INTERPRETER, "0700000030", "No Profile")
        self.assertEqual(
            self.by_name()["No Profile"]["verification_status"], "pending"
        )

    def test_filter_by_role_accepts_the_web_name_deaf(self):
        make_user(User.Role.DEAF_HOH, "0700000040", "Deaf One")
        make_interpreter("0700000041", VerificationStatus.APPROVED, full_name="Int One")
        self.assertEqual(list(self.by_name(role="deaf")), ["Deaf One"])
        self.assertEqual(list(self.by_name(role="interpreter")), ["Int One"])
        self.assertEqual(list(self.by_name(role="admin")), ["Ada Admin"])

    def test_filter_by_status(self):
        make_interpreter("0700000050", VerificationStatus.INACTIVE, full_name="Waiting")
        make_interpreter("0700000051", VerificationStatus.APPROVED, full_name="Done")
        self.assertEqual(list(self.by_name(status="pending")), ["Waiting"])
        self.assertNotIn("Waiting", self.by_name(status="verified"))

    def test_search_by_name_email_and_phone(self):
        make_user(
            User.Role.DEAF_HOH,
            "0772123456",
            "Grace Nakato",
            email="grace@example.com",
        )
        make_user(User.Role.DEAF_HOH, "0700000060", "Someone Else")
        self.assertEqual(list(self.by_name(search="nakato")), ["Grace Nakato"])
        self.assertEqual(list(self.by_name(search="grace@ex")), ["Grace Nakato"])
        self.assertEqual(list(self.by_name(search="772123")), ["Grace Nakato"])
        self.assertEqual(list(self.by_name(search="0772123456")), ["Grace Nakato"])
        self.assertEqual(self.by_name(search="nobody"), {})

    def test_filter_by_registration_range(self):
        old = make_user(User.Role.DEAF_HOH, "0700000070", "Old Timer")
        old.date_joined = timezone.now() - timedelta(days=40)
        old.save(update_fields=["date_joined"])
        mid = make_user(User.Role.DEAF_HOH, "0700000071", "Mid Timer")
        mid.date_joined = timezone.now() - timedelta(days=10)
        mid.save(update_fields=["date_joined"])
        make_user(User.Role.DEAF_HOH, "0700000072", "New Timer")
        self.assertNotIn("Old Timer", self.by_name(range="30-days"))
        self.assertIn("Mid Timer", self.by_name(range="30-days"))
        self.assertNotIn("Mid Timer", self.by_name(range="7-days"))
        self.assertIn("New Timer", self.by_name(range="7-days"))
        self.assertIn("Old Timer", self.by_name())

    def test_unknown_filter_values_are_refused(self):
        for param in ("role", "status", "range"):
            response = self.client.get(URL, {param: "nonsense"})
            self.assertEqual(response.status_code, 400, param)
            self.assertIn(param, response.json())

    def test_newest_first(self):
        first = make_user(User.Role.DEAF_HOH, "0700000080", "Earlier")
        first.date_joined = timezone.now() - timedelta(days=1)
        first.save(update_fields=["date_joined"])
        make_user(User.Role.DEAF_HOH, "0700000081", "Later")
        names = [row["full_name"] for row in self.client.get(URL).json()]
        self.assertLess(names.index("Later"), names.index("Earlier"))

    def test_each_user_appears_once(self):
        make_institution_user("0700000090", InstitutionStatus.APPROVED, full_name="Once")
        rows = self.client.get(URL).json()
        self.assertEqual([r["full_name"] for r in rows].count("Once"), 1)
