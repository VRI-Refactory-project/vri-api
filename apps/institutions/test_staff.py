"""V2-006: institution administrators create, change, revoke and reinstate staff accounts."""

import re
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.accounts.serializers import TokenPairSerializer
from apps.audit.models import AuditLog

from .models import InstitutionProfile, InstitutionStaff, InstitutionStatus, StaffRole

PASSWORD = "Sign-Connect-2026"
NEW_PASSWORD = "Doctor-Own-2026!"
STAFF_URL = reverse("institution-staff")
TEMPORARY_PASSWORD = re.compile(r"\b([A-Z2-9]{4}-[A-Z2-9]{4})\b")


def make_institution(phone, name, status=InstitutionStatus.APPROVED):
    """An institution and its registrant (first ADMIN), as registration creates them."""
    admin = User.objects.create_user(phone, PASSWORD, full_name=f"{name} admin", role=User.Role.INSTITUTION,
                                     email=f"admin{phone}@example.com")
    institution = InstitutionProfile.objects.create(user=admin, organisation_name=name, organisation_type="HOSPITAL",
                                                    status=status)
    InstitutionStaff.objects.create(institution=institution, user=admin, role=StaffRole.ADMIN)
    return admin


def staff_data(**overrides):
    data = {"full_name": "Dr Sarah Nakato", "phone_number": "0772111000", "email": "sarah@mulago.example",
            "job_title": "Doctor"}
    data.update(overrides)
    return data


def detail(name, pk):
    """URL of one staff member's endpoint: "detail", "revoke" or "reinstate"."""
    return reverse(f"institution-staff-{name}", args=[pk])


def emailed_password():
    return TEMPORARY_PASSWORD.search(mail.outbox[-1].body).group(1)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class StaffTestCase(APITestCase):
    def setUp(self):
        cache.clear()  # reset rate limits
        self.admin = make_institution("0700000100", "Mulago Hospital")
        self.institution = self.admin.institution_staff.institution
        self.sign_in_as(self.admin)

    def sign_in_as(self, user):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {TokenPairSerializer.for_user(user)['access']}")

    def add_staff(self, **overrides):
        res = self.client.post(STAFF_URL, staff_data(**overrides), format="json")
        self.assertEqual(res.status_code, 201, res.data)
        return InstitutionStaff.objects.get(pk=res.data["id"])

    def login(self, identifier, password):
        self.client.credentials()
        return self.client.post(reverse("auth-login"), {"identifier": identifier, "password": password},
                                format="json")


class RegistrantIsAdminTests(StaffTestCase):
    def test_registering_an_institution_makes_the_registrant_its_admin(self):
        self.client.credentials()
        res = self.client.post(reverse("auth-register-institution"), {
            "full_name": "Grace Namutebi", "phone_number": "0772555000", "email": "grace@nsambya.example",
            "password": PASSWORD, "organisation_name": "Nsambya Hospital", "organisation_type": "HOSPITAL",
        }, format="json")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["user"]["institution_role"], "ADMIN")
        membership = InstitutionStaff.objects.get(user__email="grace@nsambya.example")
        self.assertEqual(membership.role, StaffRole.ADMIN)
        self.assertEqual(membership.institution.organisation_name, "Nsambya Hospital")


class CreateStaffTests(StaffTestCase):
    """V2-006-BE-01 (create)."""

    def test_admin_adds_a_staff_member_and_their_login_details_are_emailed(self):
        res = self.client.post(STAFF_URL, staff_data(), format="json")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["role"], "STAFF")
        self.assertEqual(res.data["job_title"], "Doctor")
        self.assertEqual(res.data["status"], "ACTIVE")
        self.assertNotIn("password", str(res.data).lower())

        membership = InstitutionStaff.objects.get(pk=res.data["id"])
        self.assertEqual(membership.institution, self.institution)
        self.assertEqual(membership.created_by, self.admin)
        self.assertEqual(membership.user.role, User.Role.INSTITUTION)
        self.assertTrue(membership.user.must_change_password)

        email = mail.outbox[0]
        self.assertEqual(email.to, ["sarah@mulago.example"])
        self.assertIn("Mulago Hospital", email.body)
        self.assertTrue(membership.user.check_password(emailed_password()))
        self.assertTrue(AuditLog.objects.filter(event=AuditLog.Event.STAFF_CREATED, user=self.admin,
                                                target_user=membership.user).exists())

    def test_admin_can_add_another_admin(self):
        self.assertEqual(self.add_staff(role="ADMIN").role, StaffRole.ADMIN)

    def test_new_staff_sign_in_with_the_emailed_password_and_must_change_it(self):
        self.add_staff()
        res = self.login("sarah@mulago.example", emailed_password())
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["user"]["must_change_password"])
        self.assertEqual(res.data["user"]["institution_role"], "STAFF")
        self.assertEqual(res.data["user"]["institution_status"], "APPROVED")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
        changed = self.client.post(reverse("auth-password-change"),
                                   {"current_password": emailed_password(), "new_password": NEW_PASSWORD},
                                   format="json")
        self.assertEqual(changed.status_code, 200, changed.data)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {changed.data['access']}")
        me = self.client.get(reverse("institution-me"))
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.data["organisation_name"], "Mulago Hospital")

    def test_same_validation_as_registration(self):
        self.add_staff()
        duplicate = self.client.post(STAFF_URL, staff_data(email="other@mulago.example"), format="json")
        self.assertIn("phone_number", duplicate.data)
        no_email = self.client.post(STAFF_URL, staff_data(phone_number="0772111001", email=""), format="json")
        self.assertIn("email", no_email.data)
        bad_role = self.client.post(STAFF_URL, staff_data(phone_number="0772111002", email="b@x.example",
                                                          role="OWNER"), format="json")
        self.assertIn("role", bad_role.data)
        self.assertEqual(InstitutionStaff.objects.filter(institution=self.institution).count(), 2)

    def test_nothing_is_created_if_the_email_cannot_be_sent(self):
        with mock.patch("apps.institutions.staff.send_mail", side_effect=OSError("smtp down")):
            res = self.client.post(STAFF_URL, staff_data(), format="json")
        self.assertEqual(res.status_code, 503)
        self.assertFalse(User.objects.filter(email="sarah@mulago.example").exists())
        self.assertFalse(AuditLog.objects.filter(event=AuditLog.Event.STAFF_CREATED).exists())


class ListAndIsolationTests(StaffTestCase):
    """Organisation scope (SRS FR-INS-006, AT-08)."""

    def setUp(self):
        super().setUp()
        self.sarah = self.add_staff()
        self.other_admin = make_institution("0700000200", "Nsambya Hospital")
        self.sign_in_as(self.other_admin)
        self.other_staff = self.add_staff(full_name="Officer Okello", phone_number="0772222000",
                                          email="okello@nsambya.example")
        self.sign_in_as(self.admin)

    def test_list_shows_only_my_institutions_staff(self):
        res = self.client.get(STAFF_URL)
        self.assertEqual(res.status_code, 200)
        self.assertEqual({s["full_name"] for s in res.data}, {"Mulago Hospital admin", "Dr Sarah Nakato"})

    def test_another_institutions_staff_are_not_found(self):
        pk = self.other_staff.pk
        self.assertEqual(self.client.get(detail("detail", pk)).status_code, 404)
        self.assertEqual(self.client.patch(detail("detail", pk), {"job_title": "X"}, format="json").status_code, 404)
        self.assertEqual(self.client.post(detail("revoke", pk)).status_code, 404)
        self.other_staff.user.refresh_from_db()
        self.assertTrue(self.other_staff.user.is_active)

    def test_filter_by_status(self):
        self.client.post(detail("revoke", self.sarah.pk))
        revoked = self.client.get(STAFF_URL, {"status": "REVOKED"})
        self.assertEqual([s["full_name"] for s in revoked.data], ["Dr Sarah Nakato"])
        active = self.client.get(STAFF_URL, {"status": "ACTIVE"})
        self.assertEqual([s["full_name"] for s in active.data], ["Mulago Hospital admin"])
        self.assertEqual(self.client.get(STAFF_URL, {"status": "GONE"}).status_code, 400)


class RolePermissionTests(StaffTestCase):
    """V2-006-BE-02: what each role may do."""

    def test_staff_cannot_manage_staff_but_can_use_their_institution(self):
        sarah = self.add_staff()
        sarah.user.must_change_password = False
        sarah.user.save()
        self.sign_in_as(sarah.user)
        for res in [self.client.get(STAFF_URL), self.client.post(STAFF_URL, staff_data(phone_number="0772333000"),
                                                                 format="json"),
                    self.client.post(detail("revoke", self.admin.institution_staff.pk))]:
            self.assertEqual(res.status_code, 403)
            self.assertEqual(res.data["code"], "institution_admin_required")
        self.assertEqual(self.client.get(reverse("institution-me")).status_code, 200)

    def test_promoting_staff_to_admin_lets_them_manage_staff(self):
        sarah = self.add_staff()
        sarah.user.must_change_password = False
        sarah.user.save()
        self.client.patch(detail("detail", sarah.pk), {"role": "ADMIN"}, format="json")
        self.sign_in_as(sarah.user)
        self.assertEqual(self.client.get(STAFF_URL).status_code, 200)

    def test_pending_and_rejected_institutions_cannot_manage_staff(self):
        for status, code in [(InstitutionStatus.PENDING, "institution_pending"),
                             (InstitutionStatus.REJECTED, "institution_rejected")]:
            with self.subTest(status=status):
                InstitutionProfile.objects.filter(pk=self.institution.pk).update(status=status)
                res = self.client.get(STAFF_URL)
                self.assertEqual(res.status_code, 403)
                self.assertEqual(res.data["code"], code)

    def test_other_roles_are_blocked(self):
        for i, role in enumerate([User.Role.ADMIN, User.Role.INTERPRETER, User.Role.DEAF_HOH]):
            with self.subTest(role=role):
                self.sign_in_as(User.objects.create_user(f"07009000{i:02d}", PASSWORD, full_name="X", role=role))
                self.assertEqual(self.client.get(STAFF_URL).status_code, 403)
        self.client.credentials()
        self.assertEqual(self.client.get(STAFF_URL).status_code, 401)

    def test_me_shows_the_institution_role(self):
        self.assertEqual(self.client.get(reverse("auth-me")).data["institution_role"], "ADMIN")
        self.sign_in_as(User.objects.create_user("0700900099", PASSWORD, full_name="D", role=User.Role.DEAF_HOH))
        self.assertIsNone(self.client.get(reverse("auth-me")).data["institution_role"])


class UpdateStaffTests(StaffTestCase):
    """V2-006-BE-01 (update)."""

    def test_admin_changes_name_job_title_and_role(self):
        sarah = self.add_staff()
        res = self.client.patch(detail("detail", sarah.pk),
                                {"full_name": "Dr Sarah N. Nakato", "job_title": "Senior doctor", "role": "ADMIN"},
                                format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual((res.data["full_name"], res.data["job_title"], res.data["role"]),
                         ("Dr Sarah N. Nakato", "Senior doctor", "ADMIN"))
        self.assertTrue(AuditLog.objects.filter(event=AuditLog.Event.STAFF_UPDATED, target_user=sarah.user).exists())

    def test_phone_and_email_cannot_be_changed_by_the_admin(self):
        sarah = self.add_staff()
        self.client.patch(detail("detail", sarah.pk), {"email": "new@x.example", "phone_number": "0772999999"},
                          format="json")
        sarah.user.refresh_from_db()
        self.assertEqual(sarah.user.email, "sarah@mulago.example")
        self.assertEqual(str(sarah.user.phone_number), "+256772111000")

    def test_the_last_admin_cannot_be_demoted(self):
        res = self.client.patch(detail("detail", self.admin.institution_staff.pk), {"role": "STAFF"}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("at least one active administrator", res.data["detail"])
        self.add_staff(role="ADMIN")
        res = self.client.patch(detail("detail", self.admin.institution_staff.pk), {"role": "STAFF"}, format="json")
        self.assertEqual(res.status_code, 200)


class RevokeTests(StaffTestCase):
    """V2-006-BE-01 (revoke) and V2-006-BE-03 (blocked immediately)."""

    def setUp(self):
        super().setUp()
        self.sarah = self.add_staff()
        signed_in = self.login("sarah@mulago.example", emailed_password())
        self.sarah_access, self.sarah_refresh = signed_in.data["access"], signed_in.data["refresh"]
        self.sign_in_as(self.admin)

    def test_revoking_blocks_sign_in_and_ends_open_sessions_at_once(self):
        res = self.client.post(detail("revoke", self.sarah.pk))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["status"], "REVOKED")

        # The access token she already has stops working straight away, not after it expires.
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.sarah_access}")
        self.assertEqual(self.client.get(reverse("auth-me")).status_code, 401)
        # Her refresh token is cancelled, and she cannot sign in again.
        refreshed = self.client.post(reverse("auth-token-refresh"), {"refresh": self.sarah_refresh}, format="json")
        self.assertEqual(refreshed.status_code, 401)
        self.assertEqual(self.login("sarah@mulago.example", emailed_password()).status_code, 400)

        self.sarah.refresh_from_db()
        self.assertEqual(self.sarah.revoked_by, self.admin)
        self.assertFalse(self.sarah.user.is_active)
        self.assertTrue(User.objects.filter(pk=self.sarah.user_id).exists())  # kept, not deleted
        self.assertTrue(AuditLog.objects.filter(event=AuditLog.Event.STAFF_REVOKED, user=self.admin,
                                                target_user=self.sarah.user).exists())

    def test_revoking_twice_changes_nothing(self):
        self.client.post(detail("revoke", self.sarah.pk))
        self.assertEqual(self.client.post(detail("revoke", self.sarah.pk)).status_code, 200)
        self.assertEqual(AuditLog.objects.filter(event=AuditLog.Event.STAFF_REVOKED).count(), 1)

    def test_admin_cannot_revoke_themselves(self):
        res = self.client.post(detail("revoke", self.admin.institution_staff.pk))
        self.assertEqual(res.status_code, 400)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_another_admin_can_be_revoked_while_one_remains(self):
        second_admin = self.add_staff(full_name="Second Admin", phone_number="0772444000",
                                      email="second@mulago.example", role="ADMIN")
        self.assertEqual(self.client.post(detail("revoke", second_admin.pk)).status_code, 200)

    def test_reinstating_gives_access_back(self):
        self.client.post(detail("revoke", self.sarah.pk))
        res = self.client.post(detail("reinstate", self.sarah.pk))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["status"], "ACTIVE")
        self.assertEqual(self.login("sarah@mulago.example", emailed_password()).status_code, 200)
        self.assertTrue(AuditLog.objects.filter(event=AuditLog.Event.STAFF_REINSTATED,
                                                target_user=self.sarah.user).exists())
