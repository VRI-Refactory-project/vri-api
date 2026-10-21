"""V2-024: who may request an interpreter, requester cancel, and what happens when nobody can take a request."""

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.audit.models import AuditLog
from apps.institutions.models import InstitutionProfile, InstitutionStaff, InstitutionStatus, StaffRole
from apps.service_requests.models import ServiceRequest
from apps.service_requests.test_offers import make_user

User = get_user_model()
Status = ServiceRequest.Status


def make_institution_staff(institution_status=InstitutionStatus.APPROVED, role=StaffRole.STAFF):
    """A member of an institution. The institution's registrant is its first admin, as in registration."""
    registrant = make_user(User.Role.INSTITUTION)
    institution = InstitutionProfile.objects.create(
        user=registrant,
        organisation_name="Mulago Hospital",
        organisation_type="HOSPITAL",
        status=institution_status,
    )
    InstitutionStaff.objects.create(institution=institution, user=registrant, role=StaffRole.ADMIN)
    if role == StaffRole.ADMIN:
        return registrant
    staff = make_user(User.Role.INSTITUTION)
    InstitutionStaff.objects.create(institution=institution, user=staff, role=StaffRole.STAFF)
    return staff


class WhoMayRequestTests(APITestCase):
    """Only Deaf users and staff of an approved institution may ask for an interpreter."""

    def setUp(self):
        self.url = reverse("service-request-list-create")

    def create_as(self, user, **body):
        self.client.force_authenticate(user)
        return self.client.post(self.url, body, format="json")

    def test_a_deaf_user_may_request(self):
        self.assertEqual(self.create_as(make_user(User.Role.DEAF_HOH)).status_code, status.HTTP_201_CREATED)

    def test_institution_staff_may_request(self):
        response = self.create_as(make_institution_staff(), priority="EMERGENCY")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["priority"], "EMERGENCY")

    def test_an_institution_admin_may_request(self):
        response = self.create_as(make_institution_staff(role=StaffRole.ADMIN))

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_the_audit_entry_records_the_requesters_role(self):
        self.create_as(make_institution_staff())

        entry = AuditLog.objects.get(event=AuditLog.Event.REQUEST_CREATED)
        self.assertEqual(entry.details["requester_role"], User.Role.INSTITUTION)

    def test_staff_of_a_pending_institution_are_told_why(self):
        response = self.create_as(make_institution_staff(InstitutionStatus.PENDING))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["code"], "institution_pending")

    def test_staff_of_a_rejected_institution_may_not_request(self):
        response = self.create_as(make_institution_staff(InstitutionStatus.REJECTED))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["code"], "institution_rejected")

    def test_other_roles_may_not_request(self):
        for role in (User.Role.INTERPRETER, User.Role.ADMIN):
            with self.subTest(role=role):
                self.assertEqual(self.create_as(make_user(role)).status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(ServiceRequest.objects.exists())

    def test_an_institution_user_without_a_membership_may_not_request(self):
        response = self.create_as(make_user(User.Role.INSTITUTION))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_can_follow_and_cancel_their_own_request(self):
        staff = make_institution_staff()
        request_id = self.create_as(staff).data["id"]

        detail = self.client.get(reverse("service-request-detail", kwargs={"pk": request_id}))
        mine = self.client.get(reverse("service-request-mine"))
        cancelled = self.client.post(reverse("service-request-cancel", kwargs={"pk": request_id}))

        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(mine.data["id"], request_id)
        self.assertEqual(cancelled.data["status"], Status.CANCELLED)

    def test_staff_cannot_see_a_colleagues_request(self):
        staff = make_institution_staff()
        request_id = self.create_as(staff).data["id"]
        colleague = make_user(User.Role.INSTITUTION)
        InstitutionStaff.objects.create(
            institution=staff.institution_staff.institution, user=colleague, role=StaffRole.STAFF
        )
        self.client.force_authenticate(colleague)

        response = self.client.get(reverse("service-request-detail", kwargs={"pk": request_id}))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
