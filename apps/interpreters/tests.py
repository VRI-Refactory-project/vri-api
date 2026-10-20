import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from .models import (
    CertificationDocument,
    InterpreterAvailability,
    InterpreterProfile,
)

User = get_user_model()
PASSWORD = "Sign-Connect-2026"


class InterpreterProfileAccessTests(APITestCase):
    def setUp(self):
        self.interpreter = User.objects.create_user(
            "0772000001",
            PASSWORD,
            full_name="Test Interpreter",
            email="profile-interpreter@example.com",
            role=User.Role.INTERPRETER,
        )
        self.deaf_user = User.objects.create_user(
            "0772000002",
            PASSWORD,
            full_name="Test Deaf User",
            email="profile-deaf@example.com",
            role=User.Role.DEAF_HOH,
        )
        self.url = reverse("interpreters:interpreter-profile-detail")

    def test_profile_requires_authentication(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 401)

    def test_interpreter_can_retrieve_own_profile(self):
        self.client.force_authenticate(user=self.interpreter)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200, response.content)

    def test_non_interpreter_cannot_access_interpreter_profile(self):
        self.client.force_authenticate(user=self.deaf_user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403, response.content)


class CertificationUploadTests(APITestCase):
    def setUp(self):
        # Keep uploaded test files out of the project's certifications/ folder.
        self.media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media_root)
        override.enable()
        self.addCleanup(override.disable)

        self.user = User.objects.create_user(
            "0772123456",
            PASSWORD,
            full_name="Test Interpreter",
            email="interpreter@example.com",
            role=User.Role.INTERPRETER,
        )
        self.url = reverse("interpreters:certification-upload")

    def pdf(self):
        return SimpleUploadedFile(
            "cert.pdf",
            b"%PDF-1.4 test",
            content_type="application/pdf",
        )

    def test_requires_sign_in(self):
        response = self.client.post(
            self.url,
            {"document": self.pdf()},
            format="multipart",
        )
        self.assertEqual(response.status_code, 401)

    def test_stores_type_and_organisation(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            self.url,
            {
                "document": self.pdf(),
                "certificate_type": "Sign Language Interpreter",
                "issuing_organisation": "UNAD",
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(
            response.json()["certificate_type"],
            "Sign Language Interpreter",
        )

        cert = CertificationDocument.objects.get()
        self.assertEqual(cert.issuing_organisation, "UNAD")
        self.assertEqual(
            cert.interpreter,
            InterpreterProfile.objects.get(user=self.user),
        )

    def test_type_and_organisation_are_required(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            self.url,
            {"document": self.pdf()},
            format="multipart",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("certificate_type", response.json())
        self.assertIn("issuing_organisation", response.json())
        self.assertFalse(CertificationDocument.objects.exists())

    def test_rejects_unsupported_file_types(self):
        self.client.force_authenticate(self.user)
        bad = SimpleUploadedFile(
            "cert.txt",
            b"hello",
            content_type="text/plain",
        )
        response = self.client.post(
            self.url,
            {
                "document": bad,
                "certificate_type": "Other",
                "issuing_organisation": "UNAD",
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("document", response.json())


class InterpreterAvailabilityTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            "0772123457",
            PASSWORD,
            full_name="Availability Interpreter",
            email="availability@example.com",
            role=User.Role.INTERPRETER,
        )
        self.other_interpreter = User.objects.create_user(
            "0772123458",
            PASSWORD,
            full_name="Other Interpreter",
            email="other@example.com",
            role=User.Role.INTERPRETER,
        )
        self.list_url = reverse("interpreters:availability-list-create")
        self.client.force_authenticate(self.user)

    def valid_period(self):
        return {
            "start_at": "2027-01-10T09:00:00Z",
            "end_at": "2027-01-10T10:00:00Z",
        }

    def create_period(self, user, start_at, end_at):
        profile, _ = InterpreterProfile.objects.get_or_create(user=user)
        return InterpreterAvailability.objects.create(
            interpreter=profile,
            start_at=start_at,
            end_at=end_at,
        )

    def test_interpreter_can_create_availability(self):
        response = self.client.post(
            self.list_url,
            self.valid_period(),
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(InterpreterAvailability.objects.count(), 1)

        period = InterpreterAvailability.objects.get()
        self.assertEqual(period.interpreter.user, self.user)

    def test_interpreter_can_list_own_availability(self):
        self.client.force_authenticate(self.user)
        self.client.post(
            self.list_url,
            self.valid_period(),
            format="json",
        )

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, 200, response.content)
        data = response.data
        if isinstance(data, dict) and "results" in data:
            data = data["results"]
        self.assertEqual(len(data), 1)

    def test_rejects_end_time_equal_to_start_time(self):
        period = self.valid_period()
        period["end_at"] = period["start_at"]

        response = self.client.post(
            self.list_url,
            period,
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(InterpreterAvailability.objects.exists())

    def test_rejects_end_time_before_start_time(self):
        response = self.client.post(
            self.list_url,
            {
                "start_at": "2027-01-10T10:00:00Z",
                "end_at": "2027-01-10T09:00:00Z",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(InterpreterAvailability.objects.exists())

    def test_rejects_overlapping_periods(self):
        response = self.client.post(
            self.list_url,
            self.valid_period(),
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)

        response = self.client.post(
            self.list_url,
            {
                "start_at": "2027-01-10T09:30:00Z",
                "end_at": "2027-01-10T10:30:00Z",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(InterpreterAvailability.objects.count(), 1)

    def test_interpreter_cannot_access_another_interpreters_period(self):
        period = self.create_period(
            self.other_interpreter,
            "2027-01-10T09:00:00Z",
            "2027-01-10T10:00:00Z",
        )
        detail_url = reverse(
            "interpreters:availability-detail",
            kwargs={"pk": period.pk},
        )

        response = self.client.get(detail_url)

        self.assertEqual(response.status_code, 404)

    def test_non_interpreter_cannot_create_availability(self):
        deaf_user = User.objects.create_user(
            "0772123459",
            PASSWORD,
            full_name="Test Deaf User",
            email="deaf@example.com",
            role=User.Role.DEAF_HOH,
        )
        self.client.force_authenticate(deaf_user)

        response = self.client.post(
            self.list_url,
            self.valid_period(),
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(InterpreterAvailability.objects.exists())

    def test_availability_requires_authentication(self):
        self.client.force_authenticate(user=None)

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, 401)

    def test_interpreter_can_update_and_delete_own_period(self):
        response = self.client.post(
            self.list_url,
            self.valid_period(),
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)

        period = InterpreterAvailability.objects.get()
        detail_url = reverse(
            "interpreters:availability-detail",
            kwargs={"pk": period.pk},
        )

        response = self.client.patch(
            detail_url,
            {"end_at": "2027-01-10T11:00:00Z"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)

        period.refresh_from_db()
        self.assertEqual(period.end_at.hour, 11)

        response = self.client.delete(detail_url)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(InterpreterAvailability.objects.exists())
