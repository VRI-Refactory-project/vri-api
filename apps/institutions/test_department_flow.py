from django.urls import reverse
from rest_framework.test import APITestCase
from unittest.mock import patch

from apps.accounts.models import User
from .models import (
    InstitutionDepartment,
    InstitutionProfile,
    InstitutionStaff,
    InstitutionStatus,
    StaffRole,
)


class InstitutionDepartmentFlowTests(APITestCase):
    def make_admin(self, phone, email):
        user = User.objects.create_user(
            phone,
            "Test-password-123!",
            full_name="Institution Admin",
            role=User.Role.INSTITUTION,
            email=email,
        )
        institution = InstitutionProfile.objects.create(
            user=user,
            organisation_name="Example",
            organisation_type="OTHER",
            organisation_type_other="Clinic",
            status=InstitutionStatus.APPROVED,
        )
        InstitutionStaff.objects.create(institution=institution, user=user, role=StaffRole.ADMIN)
        return user, institution

    def setUp(self):
        self.admin, self.institution = self.make_admin("+256700000301", "admin301@example.com")
        self.other_admin, self.other_institution = self.make_admin("+256700000302", "admin302@example.com")
        self.client.force_authenticate(self.admin)

    def test_profile_returns_registration_data_and_persists_edits(self):
        url = reverse("institution-profile-me")
        self.admin.email_verified_at = self.admin.date_joined
        self.admin.save(update_fields=["email_verified_at"])
        initial = self.client.get(url)
        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial.data["organisation_name"], "Example")
        self.assertEqual(initial.data["organisation_type"], "OTHER")
        self.assertEqual(initial.data["organisation_type_other"], "Clinic")
        self.assertEqual(initial.data["email"], "admin301@example.com")
        self.assertEqual(initial.data["phone_number"], "+256700000301")
        response = self.client.patch(
            url,
            {
                "organisation_name": "Updated",
                "organisation_type": "SCHOOL",
                "email": "new301@example.com",
                "phone_number": "+256700000309",
                "description": "About us",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.institution.refresh_from_db()
        self.admin.refresh_from_db()
        self.assertEqual(self.institution.organisation_name, "Updated")
        self.assertEqual(self.institution.organisation_type, "SCHOOL")
        self.assertEqual(self.institution.organisation_type_other, "")
        self.assertEqual(self.institution.description, "About us")
        self.assertEqual(self.admin.email, "new301@example.com")
        self.assertEqual(str(self.admin.phone_number), "+256700000309")
        self.assertIsNone(self.admin.email_verified_at)
        self.assertEqual(response.data["organisation_name"], "Updated")
        self.assertEqual(response.data["email"], "new301@example.com")

    def test_profile_validation_and_persistence_on_failure(self):
        url = reverse("institution-profile-me")
        other_user = User.objects.create_user(
            "+256700000310",
            "Test-password-123!",
            full_name="Another User",
            role=User.Role.INSTITUTION,
            email="used@example.com",
        )
        for payload in (
            {"organisation_name": "  "},
            {"organisation_type": "INVALID"},
            {"email": "not-an-email"},
            {"email": "used@example.com"},
            {"phone_number": "+256700000310"},
            {"phone_number": "not-a-phone"},
            {"organisation_type": "OTHER", "organisation_type_other": " "},
        ):
            with self.subTest(payload=payload):
                response = self.client.patch(url, payload, format="json")
                self.assertEqual(response.status_code, 400, response.data)
        self.institution.refresh_from_db()
        self.admin.refresh_from_db()
        self.assertEqual(self.institution.organisation_name, "Example")
        self.assertEqual(self.admin.email, "admin301@example.com")
        self.assertEqual(str(self.admin.phone_number), "+256700000301")
        self.assertEqual(other_user.email, "used@example.com")

    def test_profile_is_limited_to_admin_membership(self):
        staff_user = User.objects.create_user(
            "+256700000303",
            "Test-password-123!",
            full_name="Staff Member",
            role=User.Role.INSTITUTION,
        )
        InstitutionStaff.objects.create(institution=self.institution, user=staff_user, role=StaffRole.STAFF)
        self.client.force_authenticate(staff_user)
        self.assertEqual(self.client.get(reverse("institution-profile-me")).status_code, 403)

    def test_profile_reads_and_updates_only_the_authenticated_admin_institution(self):
        url = reverse("institution-profile-me")
        self.client.force_authenticate(self.other_admin)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["organisation_name"], self.other_institution.organisation_name)
        response = self.client.patch(url, {"organisation_name": "Second institution"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.institution.refresh_from_db()
        self.other_institution.refresh_from_db()
        self.assertEqual(self.institution.organisation_name, "Example")
        self.assertEqual(self.other_institution.organisation_name, "Second institution")

    def test_profile_requires_authentication(self):
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get(reverse("institution-profile-me")).status_code, 401)

    def test_departments_scoped_and_case_insensitive_unique(self):
        url = reverse("institution-departments")
        created = self.client.post(url, {"name": "Emergency"}, format="json")
        self.assertEqual(created.status_code, 201)
        listed = self.client.get(url)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(
            listed.data[0],
            {"id": created.data["id"], "name": "Emergency", "is_active": True},
        )
        self.assertEqual(self.client.post(url, {"name": " emergency "}, format="json").status_code, 400)
        self.assertEqual(self.client.post(url, {"name": "  "}, format="json").status_code, 400)
        detail_url = reverse("institution-department-detail", args=[listed.data[0]["id"]])
        self.assertEqual(
            detail_url,
            f"/api/v1/institutions/me/departments/{listed.data[0]['id']}/",
        )
        renamed = self.client.patch(detail_url, {"name": "Maternity"}, format="json")
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(renamed.data["name"], "Maternity")
        other_department = InstitutionDepartment.objects.create(institution=self.other_institution, name="Other")
        foreign_url = reverse("institution-department-detail", args=[other_department.pk])
        self.assertEqual(self.client.patch(foreign_url, {"name": "Taken"}, format="json").status_code, 404)
        self.assertEqual(self.client.delete(foreign_url).status_code, 404)

    def test_delete_deactivates_assigned_department_and_preserves_staff_assignment(self):
        department = InstitutionDepartment.objects.create(institution=self.institution, name="Emergency")
        staff_user = User.objects.create_user(
            "+256700000304",
            "Test-password-123!",
            full_name="Staff Member",
            role=User.Role.INSTITUTION,
        )
        InstitutionStaff.objects.create(
            institution=self.institution,
            user=staff_user,
            role=StaffRole.STAFF,
            department=department,
            job_title=department.name,
        )
        listed = self.client.get(reverse("institution-departments"))
        department_id = next(
            row["id"] for row in listed.data if row["name"] == department.name
        )
        response = self.client.delete(
            reverse("institution-department-detail", args=[department_id])
        )
        self.assertEqual(response.status_code, 204)
        department.refresh_from_db()
        membership = InstitutionStaff.objects.get(user=staff_user)
        self.assertFalse(department.is_active)
        self.assertEqual(membership.department_id, department.pk)
        active_departments = self.client.get(reverse("institution-departments"))
        self.assertNotIn(department.pk, [row["id"] for row in active_departments.data])
        staff_detail = self.client.get(
            reverse("institution-staff-detail", args=[membership.pk])
        )
        self.assertEqual(staff_detail.data["department_name"], "Emergency")

    def test_delete_deactivates_unassigned_department(self):
        department = InstitutionDepartment.objects.create(
            institution=self.institution,
            name="Unused",
        )
        listed = self.client.get(reverse("institution-departments"))
        department_id = listed.data[0]["id"]
        response = self.client.delete(
            reverse("institution-department-detail", args=[department_id])
        )
        self.assertEqual(response.status_code, 204)
        department.refresh_from_db()
        self.assertFalse(department.is_active)

    @patch("apps.institutions.staff._send_login_details")
    def test_staff_creation_requires_active_department_from_same_institution(self, send_details):
        department = InstitutionDepartment.objects.create(institution=self.institution, name="Emergency")
        url = reverse("institution-staff")
        payload = {
            "full_name": "New Staff",
            "email": "newstaff301@example.com",
            "phone_number": "+256700000305",
            "department_id": department.pk,
        }
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["department_name"], "Emergency")
        self.assertEqual(response.data["job_title"], "Emergency")

        inactive = InstitutionDepartment.objects.create(institution=self.institution, name="Closed", is_active=False)
        inactive_payload = {
            **payload,
            "phone_number": "+256700000306",
            "email": "newstaff306@example.com",
            "department_id": inactive.pk,
        }
        self.assertEqual(
            self.client.post(url, inactive_payload, format="json").status_code,
            400,
        )
        foreign = InstitutionDepartment.objects.create(institution=self.other_institution, name="Foreign")
        foreign_payload = {
            **payload,
            "phone_number": "+256700000307",
            "email": "newstaff307@example.com",
            "department_id": foreign.pk,
        }
        self.assertEqual(
            self.client.post(url, foreign_payload, format="json").status_code,
            400,
        )
        missing_payload = {
            **payload,
            "phone_number": "+256700000308",
            "email": "newstaff308@example.com",
            "department_id": 999999,
        }
        self.assertEqual(
            self.client.post(url, missing_payload, format="json").status_code,
            400,
        )
