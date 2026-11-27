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
from apps.interpreters.models import (
    InterpreterProfile,
    VerificationDecision,
    VerificationStatus,
)

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
        self.assertEqual(self.client.delete(f"{URL}999/delete/").status_code, 401)

    def test_only_administrators_can_list_users(self):
        for i, role in enumerate(User.Role.values):
            user = make_user(role, f"07000008{i}0")
            self.client.force_authenticate(user=user)
            expected = 200 if role == User.Role.ADMIN else 403
            self.assertEqual(self.client.get(URL).status_code, expected, role)
            if role != User.Role.ADMIN:
                self.assertEqual(
                    self.client.delete(f"{URL}999/delete/").status_code,
                    403,
                    role,
                )


class AdminUserListTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = make_user(User.Role.ADMIN, "0700000001", "Ada Admin")
        self.client.force_authenticate(user=self.admin)

    def by_name(self, **params):
        params.setdefault("page_size", 100)
        response = self.client.get(URL, params)
        self.assertEqual(response.status_code, 200)
        return {row["full_name"]: row for row in response.json()["results"]}

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
        inactive_rejected = make_user(
            User.Role.INTERPRETER, "0700000021", "Int Rejected and Disabled", is_active=False
        )
        InterpreterProfile.objects.create(
            user=inactive_rejected, verification_status=VerificationStatus.REJECTED
        )
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
            "Deaf Off": "deactivated",
            "Int Ok": "verified",
            "Int New": "pending",
            "Int No": "rejected",
            "Int Rejected and Disabled": "rejected",
            "Int Off": "deactivated",
            "Inst Ok": "verified",
            "Inst New": "pending",
            "Inst No": "rejected",
            "Inst Gone": "deactivated",
            "Inst Revoked": "deactivated",
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

    def test_deactivated_status_filter_accepts_current_and_legacy_values(self):
        make_user(User.Role.DEAF_HOH, "0700000052", "Disabled", is_active=False)
        self.assertEqual(list(self.by_name(status="deactivated")), ["Disabled"])
        self.assertEqual(list(self.by_name(status="suspended")), ["Disabled"])

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
        names = [
            row["full_name"]
            for row in self.client.get(URL, {"page_size": 100}).json()["results"]
        ]
        self.assertLess(names.index("Later"), names.index("Earlier"))

    def test_each_user_appears_once(self):
        make_institution_user("0700000090", InstitutionStatus.APPROVED, full_name="Once")
        rows = self.client.get(URL, {"page_size": 100}).json()["results"]
        self.assertEqual([r["full_name"] for r in rows].count("Once"), 1)

    def test_page_size_and_pagination_links(self):
        for i in range(3):
            make_user(User.Role.DEAF_HOH, f"07000002{i}0", f"Page {i}")
        first = self.client.get(URL, {"page_size": 2}).json()
        self.assertEqual(first["count"], 4)  # includes the admin
        self.assertEqual(len(first["results"]), 2)
        self.assertIsNotNone(first["next"])
        second = self.client.get(URL, {"page": 2, "page_size": 2}).json()
        self.assertEqual(len(second["results"]), 2)
        self.assertIsNotNone(second["previous"])

    def test_update_fields_and_list_reflects_changes(self):
        user = make_user(User.Role.DEAF_HOH, "0700000290", "Before", email="before@example.com")
        response = self.client.patch(f"{URL}{user.pk}/", {
            "full_name": "After", "email": "after@example.com", "phone_number": "0700000291"
        }, format="json")
        self.assertEqual(response.status_code, 200, response.json())
        user.refresh_from_db()
        self.assertEqual(
            (user.full_name, user.email, str(user.phone_number)),
            ("After", "after@example.com", "+256700000291"),
        )
        self.assertIn("After", self.by_name())

    def test_update_validation_and_delete(self):
        user = make_user(User.Role.DEAF_HOH, "0700000292", "Delete Me")
        invalid = self.client.patch(f"{URL}{user.pk}/", {"email": "not-an-email"}, format="json")
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("email", invalid.json())
        response = self.client.delete(f"{URL}{user.pk}/delete/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(User.objects.filter(pk=user.pk).exists())

    def test_activation_returns_updated_user_record(self):
        user = make_user(User.Role.DEAF_HOH, "0700000293", "Disabled", is_active=False)
        response = self.client.post(f"{URL}{user.pk}/activate/")
        self.assertEqual(response.status_code, 200, response.json())
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertEqual(response.json()["id"], user.pk)
        self.assertEqual(response.json()["verification_status"], "verified")
        self.assertEqual(set(response.json()), set(self.by_name()["Disabled"]))

    def test_activation_reactivates_deactivated_interpreter_without_approval_flow(self):
        user = make_interpreter(
            "0700000294", VerificationStatus.DEACTIVATED, is_active=False
        )
        response = self.client.post(f"{URL}{user.pk}/activate/")
        self.assertEqual(response.status_code, 200, response.json())
        user.refresh_from_db()
        user.interpreter_profile.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertEqual(user.interpreter_profile.verification_status, VerificationStatus.APPROVED)
        self.assertEqual(response.json()["verification_status"], "verified")
        self.assertEqual(
            user.interpreter_profile.decisions.get().action,
            VerificationDecision.Action.REACTIVATE,
        )

    def test_admin_cannot_deactivate_or_delete_self(self):
        for method, suffix in ((self.client.post, "deactivate/"), (self.client.delete, "delete/")):
            response = method(f"{URL}{self.admin.pk}/{suffix}")
            self.assertEqual(response.status_code, 400)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_last_active_admin_cannot_be_deactivated_or_deleted(self):
        target = make_user(User.Role.ADMIN, "0700000295", "Last Admin")
        self.admin.is_active = False
        self.admin.save(update_fields=["is_active"])
        self.client.force_authenticate(user=self.admin)

        deactivate = self.client.post(f"{URL}{target.pk}/deactivate/")
        delete = self.client.delete(f"{URL}{target.pk}/delete/")
        self.assertEqual(deactivate.status_code, 400)
        self.assertEqual(delete.status_code, 400)
        target.refresh_from_db()
        self.assertTrue(target.is_active)
        self.assertTrue(User.objects.filter(pk=target.pk).exists())
