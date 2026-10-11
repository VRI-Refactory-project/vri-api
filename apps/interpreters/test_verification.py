import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from .models import CertificationDocument, InterpreterProfile, VerificationDecision, VerificationStatus

User = get_user_model()
PASSWORD = "Sign-Connect-2026"


def make_interpreter(phone="0772123456", status=VerificationStatus.INACTIVE, **profile_fields):
    user = User.objects.create_user(
        phone, PASSWORD, full_name="Test Interpreter", email=f"i{phone}@example.com", role=User.Role.INTERPRETER
    )
    return InterpreterProfile.objects.create(user=user, verification_status=status, **profile_fields)


def make_admin(phone="0700000100"):
    return User.objects.create_superuser(phone, PASSWORD, full_name="Admin User", email=f"a{phone}@example.com")


class VerificationModelTests(TestCase):
    def test_deactivated_interpreter_cannot_be_available(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_interpreter(status=VerificationStatus.DEACTIVATED, is_available=True)

    def test_decisions_are_listed_newest_first(self):
        profile, admin = make_interpreter(), make_admin()
        first = VerificationDecision.objects.create(
            interpreter=profile, admin=admin, action=VerificationDecision.Action.REJECT, reason="Blurry scan",
            previous_status=VerificationStatus.INACTIVE, new_status=VerificationStatus.REJECTED,
        )
        second = VerificationDecision.objects.create(
            interpreter=profile, admin=admin, action=VerificationDecision.Action.APPROVE,
            previous_status=VerificationStatus.REJECTED, new_status=VerificationStatus.APPROVED,
        )
        self.assertEqual(list(profile.decisions.all()), [second, first])

    def test_audit_history_survives_user_deletion_attempts(self):
        profile, admin = make_interpreter(), make_admin()
        VerificationDecision.objects.create(
            interpreter=profile, admin=admin, action=VerificationDecision.Action.APPROVE,
            previous_status=VerificationStatus.INACTIVE, new_status=VerificationStatus.APPROVED,
        )
        with self.assertRaises(ProtectedError):
            admin.delete()
        with self.assertRaises(ProtectedError):
            profile.user.delete()
        self.assertEqual(VerificationDecision.objects.count(), 1)


class TempMediaMixin:
    """Keeps uploaded test files out of the project's media folder."""

    def use_temp_media(self):
        media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media_root, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=media_root)
        override.enable()
        self.addCleanup(override.disable)


def add_certificate(profile, content=b"%PDF-1.4 test"):
    return CertificationDocument.objects.create(
        interpreter=profile,
        document=SimpleUploadedFile("cert.pdf", content, content_type="application/pdf"),
        document_name="cert.pdf",
        certificate_type="Sign Language Interpreter",
        issuing_organisation="UNAD",
    )


class ManageReviewApiTests(TempMediaMixin, APITestCase):
    def setUp(self):
        self.use_temp_media()
        self.admin = make_admin()
        self.pending = make_interpreter("0772000001")
        self.approved = make_interpreter("0772000002", status=VerificationStatus.APPROVED)
        self.cert = add_certificate(self.pending)
        self.list_url = reverse("manage-interpreters:list")

    def as_role(self, role):
        user = User.objects.create_user("0701999999", PASSWORD, full_name="Someone", role=role)
        self.client.force_authenticate(user)

    def test_requires_sign_in(self):
        for url in (self.list_url, reverse("manage-interpreters:detail", args=[self.pending.pk]),
                    reverse("manage-interpreters:certification-file", args=[self.pending.pk, self.cert.pk])):
            self.assertEqual(self.client.get(url).status_code, 401, url)

    def test_other_roles_are_forbidden(self):
        for role in (User.Role.DEAF_HOH, User.Role.INTERPRETER, User.Role.INSTITUTION):
            User.objects.filter(phone_number="+256701999999").delete()
            self.as_role(role)
            self.assertEqual(self.client.get(self.list_url).status_code, 403, role)
            file_url = reverse("manage-interpreters:certification-file", args=[self.pending.pk, self.cert.pk])
            self.assertEqual(self.client.get(file_url).status_code, 403, role)

    def test_queue_lists_interpreters_oldest_first_and_filters_by_status(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["id"] for row in response.data], [self.pending.pk, self.approved.pk])

        response = self.client.get(self.list_url, {"status": "INACTIVE"})
        self.assertEqual([row["id"] for row in response.data], [self.pending.pk])
        self.assertEqual(response.data[0]["certification_count"], 1)
        self.assertEqual(response.data[0]["phone_number"], "+256772000001")

    def test_invalid_status_filter_is_rejected(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(self.list_url, {"status": "PENDING"}).status_code, 400)

    def test_queue_excludes_profiles_of_other_roles(self):
        deaf_user = User.objects.create_user("0701000003", PASSWORD, full_name="Deaf User", role=User.Role.DEAF_HOH)
        stray = InterpreterProfile.objects.create(user=deaf_user)
        self.client.force_authenticate(self.admin)
        ids = [row["id"] for row in self.client.get(self.list_url).data]
        self.assertNotIn(stray.pk, ids)
        detail = reverse("manage-interpreters:detail", args=[stray.pk])
        self.assertEqual(self.client.get(detail).status_code, 404)

    def test_detail_shows_certificates_with_protected_link_and_history(self):
        VerificationDecision.objects.create(
            interpreter=self.pending, admin=self.admin, action=VerificationDecision.Action.REJECT, reason="Blurry",
            previous_status=VerificationStatus.INACTIVE, new_status=VerificationStatus.REJECTED,
        )
        self.client.force_authenticate(self.admin)
        response = self.client.get(reverse("manage-interpreters:detail", args=[self.pending.pk]))
        self.assertEqual(response.status_code, 200)
        cert = response.data["certifications"][0]
        self.assertTrue(cert["file_url"].endswith(f"/api/v1/manage/interpreters/{self.pending.pk}"
                                                  f"/certifications/{self.cert.pk}/file/"))
        self.assertNotIn("document", cert)
        self.assertEqual(response.data["decisions"][0]["reason"], "Blurry")
        self.assertEqual(response.data["decisions"][0]["admin_name"], "Admin User")

    def test_admin_downloads_certificate(self):
        self.client.force_authenticate(self.admin)
        url = reverse("manage-interpreters:certification-file", args=[self.pending.pk, self.cert.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"%PDF-1.4 test")

    def test_certificate_must_belong_to_that_interpreter(self):
        self.client.force_authenticate(self.admin)
        url = reverse("manage-interpreters:certification-file", args=[self.approved.pk, self.cert.pk])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_missing_file_is_404(self):
        self.cert.document.delete(save=False)
        self.client.force_authenticate(self.admin)
        url = reverse("manage-interpreters:certification-file", args=[self.pending.pk, self.cert.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
