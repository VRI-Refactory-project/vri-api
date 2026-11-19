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
        self.assertEqual(self.client.get(url).data["organisation_type_other"], "Clinic")
        response = self.client.patch(
            url,
            {"name": "Updated", "email": "new301@example.com", "description": "About us"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.institution.refresh_from_db()
        self.admin.refresh_from_db()
        self.assertEqual(self.institution.organisation_name, "Updated")
        self.assertEqual(self.admin.email, "new301@example.com")
        self.assertEqual(response.data["name"], "Updated")

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

    def test_departments_scoped_and_case_insensitive_unique(self):
        url = reverse("institution-departments")
        created = self.client.post(url, {"name": "Emergency"}, format="json")
        self.assertEqual(created.status_code, 201)
        self.assertEqual(self.client.post(url, {"name": " emergency "}, format="json").status_code, 400)
        self.assertEqual(self.client.post(url, {"name": "  "}, format="json").status_code, 400)
        self.assertEqual(len(self.client.get(url).data), 1)
        other_department = InstitutionDepartment.objects.create(institution=self.other_institution, name="Other")
        detail_url = reverse("institution-department-detail", args=[other_department.pk])
        self.assertEqual(self.client.patch(detail_url, {"name": "Taken"}, format="json").status_code, 404)
        self.assertEqual(self.client.delete(detail_url).status_code, 404)

    def test_assigned_department_cannot_be_deleted(self):
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
        response = self.client.delete(reverse("institution-department-detail", args=[department.pk]))
        self.assertEqual(response.status_code, 400)
        self.assertTrue(InstitutionDepartment.objects.filter(pk=department.pk).exists())

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
