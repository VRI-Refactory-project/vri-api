import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from .models import CertificationDocument, InterpreterProfile

User = get_user_model()
PASSWORD = "Sign-Connect-2026"


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
            "cert.pdf", b"%PDF-1.4 test", content_type="application/pdf"
        )

    def test_requires_sign_in(self):
        response = self.client.post(self.url, {"document": self.pdf()}, format="multipart")
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
        self.assertEqual(response.json()["certificate_type"], "Sign Language Interpreter")
        cert = CertificationDocument.objects.get()
        self.assertEqual(cert.issuing_organisation, "UNAD")
        self.assertEqual(cert.interpreter, InterpreterProfile.objects.get(user=self.user))

    def test_type_and_organisation_are_required(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(self.url, {"document": self.pdf()}, format="multipart")
        self.assertEqual(response.status_code, 400)
        self.assertIn("certificate_type", response.json())
        self.assertIn("issuing_organisation", response.json())
        self.assertFalse(CertificationDocument.objects.exists())

    def test_rejects_unsupported_file_types(self):
        self.client.force_authenticate(self.user)
        bad = SimpleUploadedFile("cert.txt", b"hello", content_type="text/plain")
        response = self.client.post(
            self.url,
            {"document": bad, "certificate_type": "Other", "issuing_organisation": "UNAD"},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("document", response.json())
