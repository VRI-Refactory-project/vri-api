
"""Tests for V2-046-BE-03: institution deactivation and access revocation."""

from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken

from apps.accounts.models import User
from apps.accounts.serializers import TokenPairSerializer
from apps.audit.models import AuditLog

from .models import InstitutionProfile, InstitutionStaff, InstitutionStatus, StaffRole

PASSWORD = "Sign-Connect-2026"


class InstitutionDeactivationTests(APITestCase):
    def setUp(self):
        self.platform_admin = User.objects.create_user(
            "0700000100",
            PASSWORD,
            full_name="Platform Administrator",
            role=User.Role.ADMIN,
            email="platform-admin@example.com",
        )

        self.institution_admin = self.make_institution(
            "0700000200", "Mulago Hospital"
        )
        self.institution = self.institution_admin.institution_staff.institution

        self.staff_user = User.objects.create_user(
            "0700000300",
            PASSWORD,
            full_name="Hospital Staff",
            role=User.Role.INSTITUTION,
            email="staff@mulago.example",
        )
        self.staff_membership = InstitutionStaff.objects.create(
            institution=self.institution,
            user=self.staff_user,
            role=StaffRole.STAFF,
            created_by=self.institution_admin,
        )

        self.url = reverse(
            "institution-deactivate", args=[self.institution.pk]
        )

    def make_institution(self, phone, name):
        user = User.objects.create_user(
            phone,
            PASSWORD,
            full_name=f"{name} admin",
            role=User.Role.INSTITUTION,
            email=f"{phone}@example.com",
        )
        InstitutionProfile.objects.create(
            user=user,
            organisation_name=name,
            organisation_type="HOSPITAL",
            status=InstitutionStatus.APPROVED,
        )
        institution = InstitutionProfile.objects.get(user=user)
        InstitutionStaff.objects.create(
            institution=institution,
            user=user,
            role=StaffRole.ADMIN,
        )
        return user

    def sign_in_as(self, user):
        self.client.credentials(
            HTTP_AUTHORIZATION=(
                f"Bearer {TokenPairSerializer.for_user(user)['access']}"
            )
        )

    def test_platform_admin_deactivates_institution_and_revokes_all_staff(self):
        # Issue tokens before deactivation to test that old sessions are revoked.
        staff_access = TokenPairSerializer.for_user(self.staff_user)
        admin_access = TokenPairSerializer.for_user(self.institution_admin)

        self.sign_in_as(self.platform_admin)
        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, 200, response.data)

        self.institution.refresh_from_db()
        self.assertEqual(
            self.institution.status, InstitutionStatus.DEACTIVATED
        )

        for user_id in [
            self.institution_admin.pk,
            self.staff_user.pk,
        ]:
            user = User.objects.get(pk=user_id)
            membership = InstitutionStaff.objects.get(user=user)

            self.assertFalse(user.is_active)
            self.assertFalse(membership.is_active)
            self.assertIsNotNone(membership.revoked_at)
            self.assertEqual(membership.revoked_by, self.platform_admin)

        # Previously issued access tokens must no longer authenticate.
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {staff_access['access']}"
        )
        self.assertEqual(
            self.client.get(reverse("auth-me")).status_code, 401
        )

        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {admin_access['access']}"
        )
        self.assertEqual(
            self.client.get(reverse("auth-me")).status_code, 401
        )

        # Refresh tokens created before deactivation must be blacklisted.
        for user in [self.institution_admin, self.staff_user]:
            self.assertTrue(
                BlacklistedToken.objects.filter(
                    token__user=user
                ).exists()
            )

        audit_entry = AuditLog.objects.get(
            event=AuditLog.Event.INSTITUTION_DEACTIVATED,
            target_user=self.institution_admin,
        )
        self.assertEqual(audit_entry.user, self.platform_admin)
        self.assertEqual(
            audit_entry.details["institution_id"], self.institution.pk
        )
        self.assertEqual(audit_entry.details["staff_revoked"], 2)

    def test_non_platform_admin_cannot_deactivate_institution(self):
        self.sign_in_as(self.institution_admin)

        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, 403)
        self.institution.refresh_from_db()
        self.assertEqual(
            self.institution.status, InstitutionStatus.APPROVED
        )
        self.assertFalse(
            AuditLog.objects.filter(
                event=AuditLog.Event.INSTITUTION_DEACTIVATED
            ).exists()
        )

    def test_deactivation_requires_authentication(self):
        response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, 401)

    def test_repeated_deactivation_does_not_duplicate_audit(self):
        self.sign_in_as(self.platform_admin)

        first = self.client.post(self.url, {}, format="json")
        second = self.client.post(self.url, {}, format="json")

        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(
            AuditLog.objects.filter(
                event=AuditLog.Event.INSTITUTION_DEACTIVATED,
                target_user=self.institution_admin,
            ).count(),
            1,
        )

    def test_deactivation_does_not_affect_another_institution(self):
        other_admin = self.make_institution(
            "0700000400", "Nsambya Hospital"
        )
        other_institution = other_admin.institution_staff.institution

        self.sign_in_as(self.platform_admin)
        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, 200, response.data)

        other_admin.refresh_from_db()
        other_institution.refresh_from_db()
        other_membership = InstitutionStaff.objects.get(user=other_admin)

        self.assertTrue(other_admin.is_active)
        self.assertTrue(other_membership.is_active)
        self.assertIsNone(other_membership.revoked_at)
        self.assertEqual(
            other_institution.status, InstitutionStatus.APPROVED
        )


    def test_deactivation_preserves_existing_revocation_details(self):
        from django.utils import timezone

        original_time = timezone.now()
        previous_revoker = User.objects.create_user(
            "0700000500",
            PASSWORD,
            full_name="Previous Administrator",
            role=User.Role.ADMIN,
            email="previous-admin@example.com",
        )

        self.staff_membership.revoked_at = original_time
        self.staff_membership.revoked_by = previous_revoker
        self.staff_membership.is_active = False
        self.staff_membership.save(
            update_fields=["revoked_at", "revoked_by", "is_active"]
        )

        self.sign_in_as(self.platform_admin)
        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, 200, response.data)

        self.staff_membership.refresh_from_db()
        self.assertEqual(self.staff_membership.revoked_at, original_time)
        self.assertEqual(self.staff_membership.revoked_by, previous_revoker)

        audit_entry = AuditLog.objects.get(
            event=AuditLog.Event.INSTITUTION_DEACTIVATED,
            target_user=self.institution_admin,
        )
        # Only the institution admin's membership was newly revoked.
        self.assertEqual(audit_entry.details["staff_revoked"], 1)

