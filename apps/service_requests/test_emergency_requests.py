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


class RequesterCancelTests(APITestCase):
    """Only the requester may cancel, and only while the request is still waiting or offered."""

    def setUp(self):
        self.deaf = make_user(User.Role.DEAF_HOH)
        self.client.force_authenticate(self.deaf)
        response = self.client.post(reverse("service-request-list-create"), {"priority": "EMERGENCY"}, format="json")
        self.request_id = response.data["id"]
        self.cancel_url = reverse("service-request-cancel", kwargs={"pk": self.request_id})

    def test_cancelling_records_why(self):
        response = self.client.post(self.cancel_url)

        self.assertEqual(response.data["status"], Status.CANCELLED)
        self.assertEqual(response.data["cancel_reason"], ServiceRequest.CancelReason.REQUESTER_CANCELLED)

    def test_cancelling_is_audited(self):
        self.client.post(self.cancel_url)

        entry = AuditLog.objects.get(event=AuditLog.Event.REQUEST_CANCELLED)
        self.assertEqual(entry.user, self.deaf)
        self.assertEqual(
            entry.details,
            {
                "request_id": self.request_id,
                "reason": ServiceRequest.CancelReason.REQUESTER_CANCELLED,
                "priority": "EMERGENCY",
            },
        )

    def test_cancelling_twice_is_audited_once(self):
        self.client.post(self.cancel_url)
        self.client.post(self.cancel_url)

        self.assertEqual(AuditLog.objects.filter(event=AuditLog.Event.REQUEST_CANCELLED).count(), 1)

    def test_someone_else_cannot_cancel_it(self):
        self.client.force_authenticate(make_user(User.Role.DEAF_HOH))

        response = self.client.post(self.cancel_url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(ServiceRequest.objects.get(pk=self.request_id).status, Status.PENDING)

    def test_a_failed_request_cannot_be_cancelled(self):
        ServiceRequest.objects.filter(pk=self.request_id).update(status=Status.FAILED)

        response = self.client.post(self.cancel_url)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["code"], "not_cancellable")


class PriorityIsSetOnceTests(APITestCase):
    """The emergency flag is chosen when the request is made and can never be changed afterwards."""

    def setUp(self):
        self.client.force_authenticate(make_user(User.Role.DEAF_HOH))
        self.list_url = reverse("service-request-list-create")
        self.created = self.client.post(
            self.list_url, {"priority": "EMERGENCY", "request_key": "k1"}, format="json"
        ).data
        self.detail_url = reverse("service-request-detail", kwargs={"pk": self.created["id"]})

    def test_a_request_cannot_be_edited(self):
        for method in (self.client.patch, self.client.put):
            with self.subTest(method=method.__name__):
                response = method(self.detail_url, {"priority": "NORMAL"}, format="json")
                self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(ServiceRequest.objects.get(pk=self.created["id"]).priority, "EMERGENCY")

    def test_a_retry_cannot_change_the_priority(self):
        again = self.client.post(self.list_url, {"priority": "NORMAL", "request_key": "k1"}, format="json")

        self.assertEqual(again.data["id"], self.created["id"])
        self.assertEqual(again.data["priority"], "EMERGENCY")
