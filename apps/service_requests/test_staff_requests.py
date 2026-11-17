"""V2-013: institution staff start a session for a client with them, on behalf of their institution."""

from importlib import import_module

from django.apps import apps as installed_apps
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.institutions.models import InstitutionStaff
from apps.institutions.test_sessions import make_staff
from apps.institutions.test_staff import make_institution
from apps.interpreters.models import Specialisation
from apps.service_requests.models import ServiceRequest
from apps.service_requests.test_offers import make_interpreter, make_user

User = get_user_model()
REQUESTS_URL = reverse("service-request-list-create")
OFFERS_URL = reverse("interpreter-offer-list")


class StaffRequestTests(APITestCase):
    def setUp(self):
        self.admin = make_institution("0700000500", "Mulago Hospital")
        self.institution = self.admin.institution_staff.institution
        self.doctor = make_staff(self.admin, "Dr Sarah Nakato", "Emergency")

    def ask(self, user, **body):
        self.client.force_authenticate(user)
        return self.client.post(REQUESTS_URL, body, format="json")

    def test_a_staff_request_is_tagged_with_their_institution(self):
        response = self.ask(self.doctor, specialisation_required="MEDICAL")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            response.data["institution"],
            {"id": self.institution.pk, "name": "Mulago Hospital"},
        )
        saved = ServiceRequest.objects.get(pk=response.data["id"])
        self.assertEqual(saved.institution, self.institution)
        self.assertEqual(saved.specialisation_required, Specialisation.MEDICAL)

    def test_a_deaf_users_request_has_no_institution(self):
        response = self.ask(make_user(User.Role.DEAF_HOH))

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(response.data["institution"])

    def test_the_institution_cannot_be_chosen_by_the_app(self):
        other_admin = make_institution("0700000501", "Nsambya Hospital")
        other = other_admin.institution_staff.institution

        response = self.ask(self.doctor, institution=other.pk)

        self.assertEqual(response.data["institution"]["id"], self.institution.pk)

    def test_from_request_to_accepted_session(self):
        """BE-03 and BE-04: the tagged request is matched and offered like any other, and the session
        that starts belongs to the staff member and their institution."""
        interpreter = make_interpreter(Specialisation.MEDICAL)
        request_id = self.ask(self.doctor, specialisation_required="MEDICAL").data["id"]

        self.client.force_authenticate(interpreter.user)
        offers = self.client.get(OFFERS_URL).data
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0]["institutionName"], "Mulago Hospital")
        self.assertEqual(offers[0]["sector"], "Medical")
        accepted = self.client.post(
            reverse("interpreter-offer-respond", kwargs={"pk": offers[0]["id"]}),
            {"action": "accept"},
            format="json",
        )
        self.assertEqual(accepted.status_code, status.HTTP_200_OK)

        self.client.force_authenticate(self.doctor)
        session = self.client.get(
            reverse("service-request-detail", kwargs={"pk": request_id})
        ).data
        self.assertEqual(session["status"], ServiceRequest.Status.ASSIGNED)
        self.assertEqual(session["requester_id"], self.doctor.pk)
        self.assertEqual(session["institution"]["name"], "Mulago Hospital")
        self.assertEqual(session["interpreter"]["name"], interpreter.user.full_name)

    def test_the_institution_keeps_the_session_after_the_staff_member_leaves(self):
        request_id = self.ask(self.doctor).data["id"]
        InstitutionStaff.objects.filter(user=self.doctor).delete()

        self.client.force_authenticate(self.admin)
        sessions = self.client.get(reverse("institution-sessions")).data
        usage = self.client.get(reverse("institution-usage")).data

        self.assertEqual([row["id"] for row in sessions], [request_id])
        self.assertEqual(sessions[0]["staff"]["department"], "")
        self.assertEqual(usage["totals"]["sessions"], 1)
        self.assertEqual(usage["by_department"], [{"department": "", "count": 1}])


class TagExistingRequestsMigrationTests(APITestCase):
    """The migration gives requests made before V2-013 their institution."""

    def test_staff_requests_are_tagged_and_deaf_requests_are_not(self):
        admin = make_institution("0700000502", "Kiruddu Hospital")
        nurse = make_staff(admin, "Grace Achieng", "Maternity")
        staff_request = ServiceRequest.objects.create(requester=nurse)
        deaf_request = ServiceRequest.objects.create(
            requester=make_user(User.Role.DEAF_HOH)
        )

        migration = import_module(
            "apps.service_requests.migrations.0013_servicerequest_institution"
        )
        migration.tag_existing_staff_requests(installed_apps, None)

        staff_request.refresh_from_db()
        deaf_request.refresh_from_db()
        self.assertEqual(staff_request.institution, admin.institution_staff.institution)
        self.assertIsNone(deaf_request.institution)
