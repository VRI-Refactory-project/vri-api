from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.institutions.models import InstitutionProfile, OrganisationType

User = get_user_model()
URL = "/api/v1/administration/institutions/"


class AdminInstitutionEditTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("0700000101", "password", role=User.Role.ADMIN, full_name="Admin")
        self.contact = User.objects.create_user("0700000102", "password", role=User.Role.INSTITUTION, full_name="Contact", email="contact@example.com")
        self.institution = InstitutionProfile.objects.create(user=self.contact, organisation_name="Old", organisation_type=OrganisationType.HOSPITAL)
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def test_patch_persists_frontend_fields_and_list_reflects_them(self):
        response = self.client.patch(f"{URL}{self.institution.pk}/", {
            "name": "New Clinic", "registration_number": "REG-123", "sector": "OTHER",
            "sector_other": "Community health", "email": "new@example.com", "phone": "0700000103",
        }, format="json")
        self.assertEqual(response.status_code, 200, response.json())
        self.institution.refresh_from_db()
        self.contact.refresh_from_db()
        self.assertEqual(self.institution.organisation_name, "New Clinic")
        self.assertEqual(self.institution.registration_number, "REG-123")
        self.assertEqual(self.institution.organisation_type_other, "Community health")
        self.assertEqual((self.contact.email, str(self.contact.phone_number)), ("new@example.com", "+256700000103"))
        listed = self.client.get(URL).json()[0]
        self.assertEqual(listed["name"], "New Clinic")
        self.assertEqual(listed["registration_number"], "REG-123")
        self.assertEqual(listed["sector"], "OTHER")
        self.assertEqual(listed["email"], "new@example.com")

    def test_invalid_fields_return_field_errors(self):
        response = self.client.patch(f"{URL}{self.institution.pk}/", {"email": "bad", "name": " "}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("name", response.json())
        self.assertIn("email", response.json())

    def test_delete_removes_institution_and_account(self):
        response = self.client.delete(f"{URL}{self.institution.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(InstitutionProfile.objects.filter(pk=self.institution.pk).exists())
        self.assertFalse(User.objects.filter(pk=self.contact.pk).exists())
