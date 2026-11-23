"""V2-065: the first-sign-in tutorial for institution staff, and their progress through it."""

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.service_requests.test_offers import make_interpreter

from . import tutorial
from .models import InstitutionStatus, TutorialProgress
from .test_sessions import make_staff
from .test_staff import make_institution

URL = reverse("institution-staff-tutorial")


class StaffTutorialTests(APITestCase):
    def setUp(self):
        self.admin = make_institution("0700000900", "Mulago Hospital")
        self.nurse = make_staff(self.admin, "Grace Achieng", "Maternity")
        self.client.force_authenticate(self.nurse)

    def save(self, **body):
        return self.client.patch(URL, body, format="json")

    def test_a_new_staff_member_gets_the_tutorial_from_the_start(self):
        response = self.client.get(URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["show"])
        self.assertEqual(response.data["step"], 0)
        self.assertFalse(response.data["completed"])
        self.assertFalse(response.data["skipped"])
        self.assertEqual(
            [step["id"] for step in response.data["steps"]],
            [step["id"] for step in tutorial.STAFF_TUTORIAL],
        )

    def test_the_content_covers_signing_in_starting_sessions_history_and_help(self):
        ids = {step["id"] for step in self.client.get(URL).data["steps"]}

        self.assertTrue({"sign-in", "start-session", "sessions", "help"} <= ids)
        self.assertNotIn("chat", ids)  # chat is for Deaf users only

    def test_the_step_reached_is_saved_and_comes_back(self):
        self.save(step=3)

        response = self.client.get(URL)

        self.assertEqual(response.data["step"], 3)
        self.assertTrue(response.data["show"])  # still unfinished, so it opens again where it stopped

    def test_a_step_past_the_end_is_kept_at_the_last_one(self):
        response = self.save(step=99)

        self.assertEqual(response.data["step"], tutorial.LAST_STEP)

    def test_finishing_stops_it_opening(self):
        response = self.save(step=tutorial.LAST_STEP, action="complete")

        self.assertTrue(response.data["completed"])
        self.assertFalse(response.data["show"])
        self.assertFalse(self.client.get(URL).data["show"])

    def test_skipping_stops_it_opening(self):
        response = self.save(action="skip")

        self.assertTrue(response.data["skipped"])
        self.assertFalse(response.data["show"])

    def test_replaying_starts_again_from_the_first_step(self):
        self.save(step=4, action="complete")

        response = self.save(action="restart")

        self.assertEqual(response.data["step"], 0)
        self.assertFalse(response.data["completed"])
        self.assertTrue(response.data["show"])

    def test_progress_is_kept_per_staff_member(self):
        doctor = make_staff(self.admin, "Dr Sarah Nakato", "Emergency")
        self.save(action="skip")

        self.client.force_authenticate(doctor)
        response = self.client.get(URL)

        self.assertTrue(response.data["show"])
        self.assertEqual(TutorialProgress.objects.get(staff=self.nurse.institution_staff).step, 0)
        self.assertIsNotNone(
            TutorialProgress.objects.get(staff=self.nurse.institution_staff).skipped_at
        )

    def test_an_empty_or_wrong_update_is_refused(self):
        self.assertEqual(self.save().status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.save(action="explode").status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.save(step=-1).status_code, status.HTTP_400_BAD_REQUEST)

    def test_administrators_do_not_get_the_staff_tutorial(self):
        self.client.force_authenticate(self.admin)

        response = self.client.get(URL)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["code"], "institution_staff_only")

    def test_other_roles_and_pending_institutions_are_refused(self):
        self.client.force_authenticate(make_interpreter().user)
        self.assertEqual(self.client.get(URL).status_code, status.HTTP_403_FORBIDDEN)

        pending = make_institution("0700000901", "New Clinic", status=InstitutionStatus.PENDING)
        self.client.force_authenticate(make_staff(pending))
        self.assertEqual(self.client.get(URL).status_code, status.HTTP_403_FORBIDDEN)
