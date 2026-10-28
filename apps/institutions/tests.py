from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.audit.models import AuditLog

from .models import (
    InstitutionProfile,
    InstitutionStaff,
    InstitutionStatus,
    StaffRole,
)

PASSWORD = "Sign-Connect-2026"


class InstitutionRegistrationTests(APITestCase):
    def setUp(self):
        cache.clear()

    def data(self, **overrides):
        data = {
            "full_name": "Grace Namutebi",
            "phone_number": "0772555000",
            "email": "grace@mulago.example",
            "password": PASSWORD,
            "organisation_name": "Mulago Hospital",
            "organisation_type": "HOSPITAL",
        }
        data.update(overrides)
        return data

    def test_registrant_becomes_institution_administrator_and_is_signed_in(self):
        res = self.client.post(
            reverse("auth-register-institution"), self.data(), format="json"
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["user"]["role"], "institution")
        self.assertIn("access", res.data)
        profile = InstitutionProfile.objects.get(user__phone_number="+256772555000")
        self.assertEqual(profile.organisation_name, "Mulago Hospital")
        self.assertEqual(profile.organisation_type, "HOSPITAL")

    def test_other_type_stores_what_the_registrant_wrote(self):
        res = self.client.post(
            reverse("auth-register-institution"),
            self.data(
                organisation_type="OTHER", organisation_type_other="  Deaf school  "
            ),
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        profile = InstitutionProfile.objects.get()
        self.assertEqual(profile.organisation_type, "OTHER")
        self.assertEqual(profile.organisation_type_other, "Deaf school")

    def test_other_type_needs_a_description(self):
        for text in (
            {},
            {"organisation_type_other": ""},
            {"organisation_type_other": "   "},
        ):
            res = self.client.post(
                reverse("auth-register-institution"),
                self.data(organisation_type="OTHER", **text),
                format="json",
            )
            self.assertEqual(res.status_code, 400, text)
            self.assertIn("organisation_type_other", res.data)
        self.assertFalse(InstitutionProfile.objects.exists())

    def test_the_description_is_dropped_for_the_listed_types(self):
        res = self.client.post(
            reverse("auth-register-institution"),
            self.data(organisation_type="HOSPITAL", organisation_type_other="ignored"),
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(InstitutionProfile.objects.get().organisation_type_other, "")

    def test_institution_starts_pending_approval(self):
        self.client.post(
            reverse("auth-register-institution"), self.data(), format="json"
        )
        self.assertEqual(
            InstitutionProfile.objects.get().status, InstitutionStatus.PENDING
        )

    def test_cannot_register_as_platform_admin(self):
        res = self.client.post(
            reverse("auth-register-institution"), self.data(role="admin"), format="json"
        )
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["user"]["role"], "institution")
        self.assertFalse(User.objects.filter(role="admin").exists())

    def test_organisation_name_and_type_are_required(self):
        for field in ("organisation_name", "organisation_type"):
            data = self.data()
            del data[field]
            res = self.client.post(
                reverse("auth-register-institution"), data, format="json"
            )
            self.assertEqual(res.status_code, 400, field)
            self.assertIn(field, res.data)
        self.assertFalse(User.objects.exists())

    def test_email_is_required(self):
        data = self.data()
        del data["email"]
        res = self.client.post(
            reverse("auth-register-institution"), data, format="json"
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("email", res.data)

    def test_invalid_organisation_type_rejected(self):
        res = self.client.post(
            reverse("auth-register-institution"),
            self.data(organisation_type="CIRCUS"),
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("organisation_type", res.data)

    def test_duplicate_phone_rejected(self):
        self.client.post(
            reverse("auth-register-institution"), self.data(), format="json"
        )
        res = self.client.post(
            reverse("auth-register-institution"),
            self.data(email="other@example.com"),
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("phone_number", res.data)
        self.assertEqual(InstitutionProfile.objects.count(), 1)


class InstitutionApprovalTests(APITestCase):
    def setUp(self):
        cache.clear()

        self.admin = User.objects.create_user(
            phone_number="+256772555001",
            password=PASSWORD,
            full_name="Platform Admin",
            email="admin@example.com",
            role=User.Role.ADMIN,
        )

        self.institution_user = User.objects.create_user(
            phone_number="+256772555002",
            password=PASSWORD,
            full_name="Grace Namutebi",
            email="grace@mulago.example",
            role=User.Role.INSTITUTION,
        )

        self.profile = InstitutionProfile.objects.create(
            user=self.institution_user,
            organisation_name="Mulago Hospital",
            organisation_type="HOSPITAL",
            status=InstitutionStatus.PENDING,
        )

        self.client.force_authenticate(user=self.admin)

    def test_admin_can_list_pending_applications(self):
        response = self.client.get(reverse("institution-pending"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(
            response.data[0]["organisation_name"],
            "Mulago Hospital",
        )

    def test_admin_cannot_approve_institution_with_unverified_email(self):
        self.institution_user.email_verified_at = None
        self.institution_user.save(update_fields=["email_verified_at"])

        response = self.client.post(
            reverse("institution-approve", args=[self.profile.pk])
        )

        self.assertEqual(response.status_code, 400)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.status, InstitutionStatus.PENDING)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_admin_can_approve_and_notification_is_sent(self):
        self.institution_user.email_verified_at = timezone.now()
        self.institution_user.save(update_fields=["email_verified_at"])

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("institution-approve", args=[self.profile.pk])
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], InstitutionStatus.APPROVED)

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.status, InstitutionStatus.APPROVED)

        self.assertTrue(
            AuditLog.objects.filter(
                event=AuditLog.Event.INSTITUTION_APPROVED,
                user=self.admin,
            ).exists()
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].to,
            ["grace@mulago.example"],
        )

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_admin_can_reject_application(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("institution-reject", args=[self.profile.pk])
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], InstitutionStatus.REJECTED)

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.status, InstitutionStatus.REJECTED)
        self.assertEqual(len(mail.outbox), 1)

    def test_approval_succeeds_when_notification_email_fails(self):
        self.institution_user.email_verified_at = timezone.now()
        self.institution_user.save(update_fields=["email_verified_at"])

        with patch(
            "apps.institutions.views.send_mail",
            side_effect=OSError("SMTP server unavailable"),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    reverse("institution-approve", args=[self.profile.pk])
                )

        self.assertEqual(response.status_code, 200)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.status, InstitutionStatus.APPROVED)

    def test_rejection_succeeds_when_notification_email_fails(self):
        with patch(
            "apps.institutions.views.send_mail",
            side_effect=OSError("SMTP server unavailable"),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    reverse("institution-reject", args=[self.profile.pk])
                )

        self.assertEqual(response.status_code, 200)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.status, InstitutionStatus.REJECTED)

    def test_non_admin_cannot_approve_application(self):
        self.client.force_authenticate(user=self.institution_user)

        response = self.client.post(
            reverse("institution-approve", args=[self.profile.pk])
        )

        self.assertEqual(response.status_code, 403)

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.status, InstitutionStatus.PENDING)

    def test_already_decided_application_cannot_be_approved_again(self):
        self.profile.status = InstitutionStatus.APPROVED
        self.profile.save(update_fields=["status"])

        response = self.client.post(
            reverse("institution-approve", args=[self.profile.pk])
        )

        self.assertEqual(response.status_code, 400)

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.status, InstitutionStatus.APPROVED)


class InstitutionAccountUpdateTests(APITestCase):
    def setUp(self):
        cache.clear()

        self.admin_user = User.objects.create_user(
            phone_number="+256772555101",
            password=PASSWORD,
            full_name="Institution Admin",
            email="institution-admin@example.com",
            role=User.Role.INSTITUTION,
        )
        self.institution = InstitutionProfile.objects.create(
            user=self.admin_user,
            organisation_name="Mulago Hospital",
            organisation_type="HOSPITAL",
            status=InstitutionStatus.APPROVED,
        )
        InstitutionStaff.objects.create(
            institution=self.institution,
            user=self.admin_user,
            role=StaffRole.ADMIN,
        )
        self.url = reverse("institution-account-update")
        self.client.force_authenticate(user=self.admin_user)

    def test_admin_can_retrieve_account_details(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["organisation_name"], "Mulago Hospital"
        )
        self.assertEqual(response.data["organisation_type"], "HOSPITAL")
        self.assertNotIn("status", response.data)

    def test_admin_can_update_organisation_details(self):
        response = self.client.patch(
            self.url,
            {
                "organisation_name": "Updated Hospital",
                "organisation_type": "OTHER",
                "organisation_type_other": "Specialist medical centre",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.institution.refresh_from_db()
        self.assertEqual(
            self.institution.organisation_name, "Updated Hospital"
        )
        self.assertEqual(self.institution.organisation_type, "OTHER")
        self.assertEqual(
            self.institution.organisation_type_other,
            "Specialist medical centre",
        )

    def test_blank_organisation_name_is_rejected(self):
        response = self.client.patch(
            self.url, {"organisation_name": "   "}, format="json"
        )

        self.assertEqual(response.status_code, 400)
        self.institution.refresh_from_db()
        self.assertEqual(
            self.institution.organisation_name, "Mulago Hospital"
        )

    def test_other_type_requires_a_description(self):
        response = self.client.patch(
            self.url,
            {
                "organisation_type": "OTHER",
                "organisation_type_other": "   ",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("organisation_type_other", response.data)

    def test_approval_status_cannot_be_changed_through_update(self):
        response = self.client.patch(
            self.url, {"status": "REJECTED"}, format="json"
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.institution.refresh_from_db()
        self.assertEqual(self.institution.status, InstitutionStatus.APPROVED)

    def test_ordinary_staff_cannot_update_account(self):
        staff_user = User.objects.create_user(
            phone_number="+256772555102",
            password=PASSWORD,
            full_name="Institution Staff",
            email="institution-staff@example.com",
            role=User.Role.INSTITUTION,
        )
        InstitutionStaff.objects.create(
            institution=self.institution,
            user=staff_user,
            role=StaffRole.STAFF,
        )
        self.client.force_authenticate(user=staff_user)

        response = self.client.patch(
            self.url,
            {"organisation_name": "Unauthorised Change"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.institution.refresh_from_db()
        self.assertEqual(
            self.institution.organisation_name, "Mulago Hospital"
        )


class InstitutionDeletionTests(APITestCase):
    def setUp(self):
        cache.clear()

        # 1. Setup global platform admin user
        self.admin = User.objects.create_user(
            phone_number="+256772555001",
            password=PASSWORD,
            full_name="Platform Admin",
            email="admin@example.com",
            role=User.Role.ADMIN,
        )

        # 2. Setup standard institutional user
        self.institution_user = User.objects.create_user(
            phone_number="+256772555002",
            password=PASSWORD,
            full_name="Grace Namutebi",
            email="grace@mulago.example",
            role=User.Role.INSTITUTION,
        )

        # 3. Create an institution instance
        self.profile = InstitutionProfile.objects.create(
            user=self.institution_user,
            organisation_name="Mulago Hospital",
            organisation_type="HOSPITAL",
            status=InstitutionStatus.APPROVED,
        )

    def test_platform_admin_can_soft_delete_unused_institution(self):
        """A platform admin should be able to successfully delete an institution with no active staff."""
        self.client.force_authenticate(user=self.admin)

        url = reverse("institution-delete", args=[self.profile.pk])
        response = self.client.delete(url)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["success"])

        # Verify the soft delete status was applied
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.status, InstitutionStatus.DELETED)

    def test_cannot_delete_institution_with_active_staff(self):
        """The endpoint must block deletion if the institution has an active staff membership."""
        # Attach an active staff member to the institution profile
        InstitutionStaff.objects.create(
            institution=self.profile,
            user=self.institution_user,
            role=StaffRole.ADMIN,
            is_active=True,
        )

        self.client.force_authenticate(user=self.admin)
        url = reverse("institution-delete", args=[self.profile.pk])
        response = self.client.delete(url)

        # Should fail validation because active staff exists
        self.assertEqual(response.status_code, 400)
        self.assertIn("institution", response.data)

        # Verify the database status remains unchanged
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.status, InstitutionStatus.APPROVED)

    def test_can_delete_institution_if_all_staff_are_inactive(self):
        """If staff profiles exist but are flagged as inactive, deletion should proceed."""
        InstitutionStaff.objects.create(
            institution=self.profile,
            user=self.institution_user,
            role=StaffRole.ADMIN,
            is_active=False,
        )

        self.client.force_authenticate(user=self.admin)
        url = reverse("institution-delete", args=[self.profile.pk])
        response = self.client.delete(url)

        self.assertEqual(response.status_code, 200)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.status, InstitutionStatus.DELETED)

    def test_non_admin_users_are_forbidden_from_deleting(self):
        """Regular institution users must be blocked from triggering deletions."""
        self.client.force_authenticate(user=self.institution_user)

        url = reverse("institution-delete", args=[self.profile.pk])
        response = self.client.delete(url)

        self.assertEqual(response.status_code, 403)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.status, InstitutionStatus.APPROVED)

    def test_cannot_delete_an_already_deleted_institution(self):
        """Running a deletion on a profile already soft-deleted should yield a bad request error."""
        self.profile.status = InstitutionStatus.DELETED
        self.profile.save()

        self.client.force_authenticate(user=self.admin)
        url = reverse("institution-delete", args=[self.profile.pk])
        response = self.client.delete(url)

        self.assertEqual(response.status_code, 400)
        self.assertIn("status", response.data)
