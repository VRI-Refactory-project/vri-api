import tempfile

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.audit.models import AuditLog
from apps.institutions.models import (
    InstitutionProfile,
    InstitutionStaff,
    InstitutionStatus,
    InstitutionVerificationDocument,
    StaffRole,
)

User = get_user_model()


class AdminSettingsTests(APITestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            email="admin@signconnect.ug",
            full_name="System Admin",
            phone_number="+256700000000",
            password="OldPassword123!",
        )
        self.client.force_authenticate(user=self.admin_user)
        self.settings_url = reverse("administration:admin-settings")
        self.password_url = reverse("administration:admin-change-password")

    def test_get_admin_settings_creates_profile(self):
        response = self.client.get(self.settings_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["full_name"], "System Admin")
        self.assertTrue(response.data["session_alerts"])

    def test_update_admin_profile_and_notifications(self):
        payload = {
            "full_name": "Updated Admin Name",
            "session_alerts": False,
            "verification_alerts": True,
        }
        response = self.client.patch(self.settings_url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["full_name"], "Updated Admin Name")
        self.assertFalse(response.data["session_alerts"])

        # Check persistence on subsequent request
        get_res = self.client.get(self.settings_url)
        self.assertEqual(get_res.data["full_name"], "Updated Admin Name")

    def test_change_password_updates_timestamp(self):
        payload = {
            "old_password": "OldPassword123!",
            "new_password": "NewSecurePassword456!",
            "confirm_password": "NewSecurePassword456!",
        }
        response = self.client.post(self.password_url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.admin_user.refresh_from_db()
        self.assertTrue(self.admin_user.check_password("NewSecurePassword456!"))


class AdminInstitutionManagementTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            email="admin@signconnect.ug",
            full_name="System Admin",
            phone_number="+256700000000",
            password="AdminPassword123!",
        )
        self.institution_user = User.objects.create_user(
            email="institution@example.com",
            full_name="Institution Admin",
            phone_number="+256700000001",
            password="InstitutionPassword123!",
            role=User.Role.INSTITUTION,
        )
        self.institution = InstitutionProfile.objects.create(
            user=self.institution_user,
            organisation_name="Mulago Hospital",
            organisation_type="HOSPITAL",
        )
        self.membership = InstitutionStaff.objects.create(
            institution=self.institution,
            user=self.institution_user,
            role=StaffRole.ADMIN,
        )
        self.staff_user = User.objects.create_user(
            email="staff@example.com",
            full_name="Institution Staff",
            phone_number="+256700000002",
            password="StaffPassword123!",
            role=User.Role.INSTITUTION,
        )
        self.staff_membership = InstitutionStaff.objects.create(
            institution=self.institution,
            user=self.staff_user,
            role=StaffRole.STAFF,
        )
        self.client.force_authenticate(user=self.admin)

    def test_admin_can_retrieve_and_edit_institution(self):
        detail_url = reverse(
            "administration:admin-institution-detail", args=[self.institution.pk]
        )
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "pending")
        self.assertEqual(response.data["name"], "Mulago Hospital")
        self.assertEqual(response.data["contact_person_name"], "Institution Admin")
        self.assertEqual(response.data["phone"], "+256700000001")
        self.assertEqual(response.data["registration_number"], "")
        self.assertEqual(
            response.data["verification_progress"],
            {
                "institution_details": "completed",
                "documents": "not_started",
                "verification_checks": "in_progress",
                "approval_decision": "in_progress",
            },
        )
        platform_admin = User.objects.create_user(
            email="reviewer@signconnect.ug",
            full_name="Application Reviewer",
            phone_number="+256700000009",
            password="ReviewerPassword123!",
            role=User.Role.ADMIN,
        )
        self.client.force_authenticate(user=platform_admin)
        reviewer_response = self.client.get(detail_url)
        self.assertEqual(reviewer_response.status_code, status.HTTP_200_OK)
        self.client.force_authenticate(user=self.admin)

        response = self.client.patch(
            detail_url,
            {
                "organisation_name": "Updated Hospital",
                "organisation_type": "OTHER",
                "organisation_type_other": "Specialist centre",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.institution.refresh_from_db()
        self.assertEqual(self.institution.organisation_name, "Updated Hospital")
        self.assertEqual(self.institution.organisation_type, "OTHER")
        self.assertEqual(response.data["status"], InstitutionStatus.PENDING)

    def test_detail_lists_all_documents_and_download_is_admin_only(self):
        with tempfile.TemporaryDirectory() as media_root, override_settings(
            MEDIA_ROOT=media_root
        ):
            document = InstitutionVerificationDocument.objects.create(
                institution=self.institution,
                file=SimpleUploadedFile(
                    "registration-certificate.pdf",
                    b"%PDF-1.4\nCertificate\n%%EOF",
                    content_type="application/pdf",
                ),
                document_type="business_registration",
                issuing_authority="Uganda Registration Services Bureau",
            )
            InstitutionVerificationDocument.objects.create(
                institution=self.institution,
                file=SimpleUploadedFile(
                    "tax-clearance.pdf",
                    b"%PDF-1.4\nTax clearance\n%%EOF",
                    content_type="application/pdf",
                ),
                document_type="tax_clearance",
                issuing_authority="Uganda Revenue Authority",
            )
            url = reverse(
                "administration:admin-institution-detail",
                args=[self.institution.pk],
            )
            response = self.client.get(url)

            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(len(response.data["documents"]), 2)
            item = response.data["documents"][0]
            self.assertEqual(item["id"], document.pk)
            self.assertEqual(item["file_name"], "registration-certificate.pdf")
            self.assertEqual(item["document_type"], "business_registration")
            self.assertEqual(
                item["issuing_authority"],
                "Uganda Registration Services Bureau",
            )
            self.assertIsNotNone(item["uploaded_at"])
            self.assertIn(
                f"/administration/institutions/{self.institution.pk}/documents/{document.pk}/download/",
                item["file_url"],
            )

            download = self.client.get(item["file_url"])
            self.assertEqual(download.status_code, status.HTTP_200_OK)
            self.assertIn(
                "registration-certificate.pdf",
                download["Content-Disposition"],
            )
            self.assertEqual(
                b"".join(download.streaming_content),
                b"%PDF-1.4\nCertificate\n%%EOF",
            )

            self.client.force_authenticate(user=self.institution_user)
            denied = self.client.get(item["file_url"])
            self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        WEB_APP_URL="https://web.test",
    )
    def test_admin_approval_route_updates_status_and_sends_notification(self):
        self.institution_user.email_verified_at = timezone.now()
        self.institution_user.is_active = False
        self.institution_user.save(update_fields=["email_verified_at"])
        self.institution_user.save(update_fields=["is_active"])

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse(
                    "administration:admin-institution-approve",
                    args=[self.institution.pk],
                )
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["status"], InstitutionStatus.APPROVED)
        self.institution.refresh_from_db()
        self.assertEqual(self.institution.status, InstitutionStatus.APPROVED)
        self.institution_user.refresh_from_db()
        self.assertTrue(self.institution_user.is_active)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["institution@example.com"])
        self.assertIn("https://web.test/sign-in", mail.outbox[0].body)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        WEB_APP_URL="https://web.test",
    )
    def test_admin_rejection_route_updates_status_and_sends_notification(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse(
                    "administration:admin-institution-reject",
                    args=[self.institution.pk],
                ),
                {"rejection_reason": "Please update your registration document."},
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["status"], InstitutionStatus.REJECTED)
        self.institution.refresh_from_db()
        self.assertEqual(
            self.institution.rejection_reason,
            "Please update your registration document.",
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Please update your registration document.", mail.outbox[0].body)
        self.assertIn("https://web.test/register/institution", mail.outbox[0].body)
        self.institution_user.refresh_from_db()
        self.assertTrue(self.institution_user.is_active)

    def test_deactivation_disables_all_staff_and_records_audit_event(self):
        response = self.client.post(
            reverse(
                "administration:admin-institution-deactivate",
                args=[self.institution.pk],
            )
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertFalse(response.data["is_active"])
        self.institution_user.refresh_from_db()
        self.staff_user.refresh_from_db()
        self.membership.refresh_from_db()
        self.staff_membership.refresh_from_db()
        self.assertFalse(self.institution_user.is_active)
        self.assertFalse(self.staff_user.is_active)
        self.assertIsNotNone(self.membership.revoked_at)
        self.assertIsNotNone(self.staff_membership.revoked_at)
        self.assertTrue(
            AuditLog.objects.filter(
                event=AuditLog.Event.INSTITUTION_DEACTIVATED,
                user=self.admin,
                target_user=self.institution_user,
            ).exists()
        )
        detail_response = self.client.get(
            reverse(
                "administration:admin-institution-detail",
                args=[self.institution.pk],
            )
        )
        self.assertEqual(detail_response.data["status"], "deactivated")
