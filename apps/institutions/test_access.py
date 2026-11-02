"""V2-005: institution status in the user payload, and institution-scoped access."""

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.accounts.serializers import TokenPairSerializer
from apps.institutions.models import InstitutionStatus

from .models import InstitutionProfile, InstitutionStaff, StaffRole

PASSWORD = "Sign-Connect-2026"


def make_institution(phone, name, status):
    user = User.objects.create_user(
        phone,
        PASSWORD,
        full_name=f"{name} admin",
        role=User.Role.INSTITUTION,
        email=f"{phone}@example.com",
    )
    institution = InstitutionProfile.objects.create(
        user=user, organisation_name=name, organisation_type="HOSPITAL", status=status
    )
    # As registration does: the registrant is the institution's first administrator (V2-006).
    InstitutionStaff.objects.create(
        institution=institution, user=user, role=StaffRole.ADMIN
    )
    return user


class InstitutionAccessTests(APITestCase):
    def sign_in_as(self, user):
        self.client.cookies["active_platform_user"] = str(user.pk)
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {TokenPairSerializer.for_user(user)['access']}"
        )

    def test_me_shows_the_institution_status(self):
        for i, institution_status in enumerate(InstitutionStatus.values):
            with self.subTest(status=institution_status):
                self.sign_in_as(
                    make_institution(f"07720000{i:02d}", institution_status.title(), institution_status)
                )
                self.assertEqual(
                    self.client.get(reverse("auth-me")).data["institution_status"],
                    institution_status,
                )

    def test_other_roles_have_no_institution_status(self):
        self.sign_in_as(
            User.objects.create_user(
                "0772000099", PASSWORD, full_name="D", role=User.Role.DEAF_HOH
            )
        )
        self.assertIsNone(
            self.client.get(reverse("auth-me")).data["institution_status"]
        )

    def test_approved_institution_sees_only_its_own_institution(self):
        make_institution("0772000001", "Nsambya Hospital", InstitutionStatus.APPROVED)
        self.sign_in_as(
            make_institution(
                "0772000002", "Mulago Hospital", InstitutionStatus.APPROVED
            )
        )
        res = self.client.get(reverse("institution-me"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["organisation_name"], "Mulago Hospital")

    def test_pending_and_rejected_institutions_are_blocked_with_a_code(self):
        cases = [
            (InstitutionStatus.PENDING, "institution_pending"),
            (InstitutionStatus.REJECTED, "institution_rejected"),
        ]
        for i, (institution_status, code) in enumerate(cases):
            with self.subTest(status=institution_status):
                self.sign_in_as(
                    make_institution(f"07720001{i:02d}", institution_status.title(), institution_status)
                )
                res = self.client.get(reverse("institution-me"))
                self.assertEqual(res.status_code, 403)
                self.assertEqual(res.data["code"], code)

    def test_other_roles_are_blocked(self):
        for i, role in enumerate(
            [User.Role.ADMIN, User.Role.INTERPRETER, User.Role.DEAF_HOH]
        ):
            with self.subTest(role=role):
                self.sign_in_as(
                    User.objects.create_user(
                        f"07720002{i:02d}", PASSWORD, full_name="X", role=role
                    )
                )
                self.assertEqual(
                    self.client.get(reverse("institution-me")).status_code, 403
                )

    def test_not_signed_in_is_401(self):
        self.assertEqual(self.client.get(reverse("institution-me")).status_code, 401)


class InstitutionAccountUpdateAccessTests(APITestCase):

    def setUp(self):
        self.url = reverse("institution-account-update")

    def test_approved_institution_admin_can_update_profile(self):
        user = make_institution(
            phone="07720000011",
            name="Original Hospital",
            status=InstitutionStatus.APPROVED,
        )
        institution = InstitutionProfile.objects.get(user=user)

        self.client.force_authenticate(user=user)

        response = self.client.patch(
            self.url,
            {"organisation_name": "Updated Hospital"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        institution.refresh_from_db()
        self.assertEqual(institution.organisation_name, "Updated Hospital")

    def test_pending_institution_admin_can_update_profile(self):
        user = make_institution(
            phone="07720000012",
            name="Pending Hospital",
            status=InstitutionStatus.PENDING,
        )
        institution = InstitutionProfile.objects.get(user=user)

        self.client.force_authenticate(user=user)

        response = self.client.patch(
            self.url,
            {"organisation_name": "Updated Pending Hospital"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        institution.refresh_from_db()
        self.assertEqual(
            institution.organisation_name,
            "Updated Pending Hospital",
        )

    def test_rejected_institution_admin_can_update_profile(self):
        user = make_institution(
            phone="07720000013",
            name="Rejected Hospital",
            status=InstitutionStatus.REJECTED,
        )
        institution = InstitutionProfile.objects.get(user=user)

        self.client.force_authenticate(user=user)

        response = self.client.patch(
            self.url,
            {"organisation_name": "Updated Rejected Hospital"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        institution.refresh_from_db()
        self.assertEqual(
            institution.organisation_name,
            "Updated Rejected Hospital",
        )

    def test_deleted_institution_admin_cannot_update_profile(self):
        user = make_institution(
            phone="07720000014",
            name="Deleted Hospital",
            status=InstitutionStatus.DELETED,
        )
        institution = InstitutionProfile.objects.get(user=user)

        self.client.force_authenticate(user=user)

        response = self.client.patch(
            self.url,
            {"organisation_name": "Attempted Update"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        institution.refresh_from_db()
        self.assertEqual(institution.organisation_name, "Deleted Hospital")

    def test_unauthenticated_user_cannot_update_profile(self):
        response = self.client.patch(
            self.url,
            {"organisation_name": "Unauthorized Update"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
