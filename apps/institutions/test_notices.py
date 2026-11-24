
"""Tests for institution-wide notices."""

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.accounts.serializers import TokenPairSerializer

from .models import (
    InstitutionNotice,
    InstitutionProfile,
    InstitutionStaff,
    InstitutionStatus,
    StaffRole,
)

PASSWORD = "Sign-Connect-2026"
NOTICES_URL = reverse("institution-notices")


def make_institution(phone, name, status=InstitutionStatus.APPROVED):
    """Create an institution with its registering administrator."""
    admin = User.objects.create_user(
        phone,
        PASSWORD,
        full_name=f"{name} admin",
        role=User.Role.INSTITUTION,
        email=f"admin{phone}@example.com",
    )
    institution = InstitutionProfile.objects.create(
        user=admin,
        organisation_name=name,
        organisation_type="HOSPITAL",
        status=status,
    )
    InstitutionStaff.objects.create(
        institution=institution,
        user=admin,
        role=StaffRole.ADMIN,
    )
    return admin


class InstitutionNoticeTests(APITestCase):
    def setUp(self):
        self.admin = make_institution("0700000100", "Mulago Hospital")
        self.institution = self.admin.institution_staff.institution

        self.staff_user = User.objects.create_user(
            "0700000101",
            PASSWORD,
            full_name="Dr Sarah Nakato",
            role=User.Role.INSTITUTION,
            email="sarah@mulago.example",
        )
        self.staff_membership = InstitutionStaff.objects.create(
            institution=self.institution,
            user=self.staff_user,
            role=StaffRole.STAFF,
        )

        self.sign_in_as(self.admin)

    def sign_in_as(self, user):
        self.client.cookies["active_platform_user"] = str(user.pk)
        self.client.credentials(
            HTTP_AUTHORIZATION=(
                f"Bearer {TokenPairSerializer.for_user(user)['access']}"
            )
        )

    def notice_detail_url(self, notice):
        return reverse("institution-notice-detail", args=[notice.pk])

    def create_notice(self, title="Clinic Update", message="New clinic hours"):
        return InstitutionNotice.objects.create(
            institution=self.institution,
            title=title,
            message=message,
            created_by=self.admin,
        )

    def test_admin_can_list_notices(self):
        notice = self.create_notice()

        response = self.client.get(NOTICES_URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["id"], notice.pk)
        self.assertEqual(response.data[0]["title"], "Clinic Update")

    def test_admin_can_create_notice(self):
        response = self.client.post(
            NOTICES_URL,
            {
                "title": "Staff Meeting",
                "message": "Meeting at 10 AM tomorrow.",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        notice = InstitutionNotice.objects.get(pk=response.data["id"])
        self.assertEqual(notice.institution, self.institution)
        self.assertEqual(notice.created_by, self.admin)
        self.assertEqual(notice.title, "Staff Meeting")

    def test_admin_cannot_spoof_notice_creator_or_institution(self):
        other_admin = make_institution("0700000202", "Nsambya Hospital")

        response = self.client.post(
            NOTICES_URL,
            {
                "title": "Security Test",
                "message": "Check server-controlled fields.",
                "created_by": other_admin.pk,
                "institution": other_admin.institution_staff.institution.pk,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)

        notice = InstitutionNotice.objects.get(pk=response.data["id"])
        self.assertEqual(notice.created_by, self.admin)
        self.assertEqual(notice.institution, self.institution)

    def test_admin_can_update_notice(self):
        notice = self.create_notice()

        response = self.client.patch(
            self.notice_detail_url(notice),
            {"title": "Updated Clinic Hours"},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        notice.refresh_from_db()
        self.assertEqual(notice.title, "Updated Clinic Hours")
        self.assertEqual(notice.message, "New clinic hours")

    def test_admin_can_delete_notice(self):
        notice = self.create_notice()

        response = self.client.delete(self.notice_detail_url(notice))

        self.assertEqual(response.status_code, 204)
        self.assertFalse(InstitutionNotice.objects.filter(pk=notice.pk).exists())

    def test_active_staff_can_list_notices(self):
        self.create_notice()
        self.sign_in_as(self.staff_user)

        response = self.client.get(NOTICES_URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

    def test_staff_cannot_create_notices(self):
        self.sign_in_as(self.staff_user)

        response = self.client.post(
            NOTICES_URL,
            {"title": "Staff Notice", "message": "A message"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(InstitutionNotice.objects.count(), 0)

    def test_staff_cannot_update_or_delete_notices(self):
        notice = self.create_notice()
        self.sign_in_as(self.staff_user)

        update_response = self.client.patch(
            self.notice_detail_url(notice),
            {"title": "Changed"},
            format="json",
        )
        delete_response = self.client.delete(self.notice_detail_url(notice))

        self.assertEqual(update_response.status_code, 403)
        self.assertEqual(delete_response.status_code, 403)
        self.assertTrue(InstitutionNotice.objects.filter(pk=notice.pk).exists())

    def test_list_only_returns_notices_from_my_institution(self):
        other_admin = make_institution("0700000200", "Nsambya Hospital")
        other_notice = InstitutionNotice.objects.create(
            institution=other_admin.institution_staff.institution,
            title="Other Hospital Notice",
            message="This must remain private.",
            created_by=other_admin,
        )
        own_notice = self.create_notice()

        response = self.client.get(NOTICES_URL)

        self.assertEqual(response.status_code, 200)
        returned_ids = {item["id"] for item in response.data}
        self.assertIn(own_notice.pk, returned_ids)
        self.assertNotIn(other_notice.pk, returned_ids)

    def test_cannot_update_or_delete_another_institutions_notice(self):
        other_admin = make_institution("0700000201", "Nsambya Hospital")
        other_notice = InstitutionNotice.objects.create(
            institution=other_admin.institution_staff.institution,
            title="Private Notice",
            message="Another institution's notice.",
            created_by=other_admin,
        )

        update_response = self.client.patch(
            self.notice_detail_url(other_notice),
            {"title": "Attempted change"},
            format="json",
        )
        delete_response = self.client.delete(self.notice_detail_url(other_notice))

        self.assertEqual(update_response.status_code, 404)
        self.assertEqual(delete_response.status_code, 404)
        self.assertTrue(
            InstitutionNotice.objects.filter(pk=other_notice.pk).exists()
        )

    def test_inactive_membership_cannot_access_notices(self):
        self.staff_membership.is_active = False
        self.staff_membership.save(update_fields=["is_active"])
        self.sign_in_as(self.staff_user)

        response = self.client.get(NOTICES_URL)

        self.assertEqual(response.status_code, 403)

    def test_revoked_member_cannot_access_notices(self):
        self.staff_membership.revoked_at = timezone.now()
        self.staff_membership.save(update_fields=["revoked_at"])
        self.sign_in_as(self.staff_user)

        response = self.client.get(NOTICES_URL)

        self.assertEqual(response.status_code, 403)

    def test_pending_and_rejected_institutions_cannot_access_notices(self):
        for institution_status in [
            InstitutionStatus.PENDING,
            InstitutionStatus.REJECTED,
        ]:
            with self.subTest(status=institution_status):
                InstitutionProfile.objects.filter(
                    pk=self.institution.pk
                ).update(status=institution_status)

                response = self.client.get(NOTICES_URL)

                self.assertEqual(response.status_code, 403)
