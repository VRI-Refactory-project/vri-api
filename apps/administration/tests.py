from django.contrib.auth import get_user_model
from django.core import mail
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
        self.assertEqual(response.data["status"], InstitutionStatus.PENDING)

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

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_admin_approval_route_updates_status_and_sends_notification(self):
        self.institution_user.email_verified_at = timezone.now()
        self.institution_user.save(update_fields=["email_verified_at"])

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
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["institution@example.com"])

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_admin_rejection_route_updates_status_and_sends_notification(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse(
                    "administration:admin-institution-reject",
                    args=[self.institution.pk],
                )
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["status"], InstitutionStatus.REJECTED)
        self.assertEqual(len(mail.outbox), 1)

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
