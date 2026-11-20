
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.interpreters.models import Specialisation
from apps.realtime.models import VideoSession
from apps.service_requests.models import ServiceRequest
from apps.service_requests.test_offers import make_interpreter, make_user

User = get_user_model()


class InterpreterSessionHistoryTests(APITestCase):

    def setUp(self):
        self.interpreter = make_interpreter()
        self.other_interpreter = make_interpreter()

        self.deaf_user = make_user(User.Role.DEAF_HOH)
        self.other_deaf_user = make_user(User.Role.DEAF_HOH)

        self.url = reverse("interpreters:interpreter-session-history")

        self.client.force_authenticate(user=self.interpreter.user)

    def create_request(
        self,
        *,
        interpreter=None,
        requester=None,
        request_status=ServiceRequest.Status.ASSIGNED,
        specialisation=Specialisation.GENERAL,
        assigned_at=None,
        ended_at=None,
    ):
        return ServiceRequest.objects.create(
            requester=requester or self.deaf_user,
            interpreter=interpreter or self.interpreter,
            status=request_status,
            specialisation_required=specialisation,
            assigned_at=assigned_at or timezone.now(),
            ended_at=ended_at,
        )

    # --------------------------------------------------
    # Authentication and permissions
    # --------------------------------------------------

    def test_session_history_requires_authentication(self):
        self.client.force_authenticate(user=None)

        response = self.client.get(self.url)

        self.assertEqual(
            response.status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    def test_only_interpreters_can_access_session_history(self):
        self.client.force_authenticate(user=self.deaf_user)

        response = self.client.get(self.url)

        self.assertEqual(
            response.status_code,
            status.HTTP_403_FORBIDDEN,
        )

    # --------------------------------------------------
    # Interpreter session visibility
    # --------------------------------------------------

    def test_interpreter_can_retrieve_own_session_history(self):
        own_request = self.create_request()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["id"], own_request.pk)

    def test_interpreter_cannot_see_another_interpreters_sessions(self):
        own_request = self.create_request()

        other_request = self.create_request(
            interpreter=self.other_interpreter,
            requester=self.other_deaf_user,
        )

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        returned_ids = [
            session["id"]
            for session in response.data
        ]

        self.assertIn(own_request.pk, returned_ids)
        self.assertNotIn(other_request.pk, returned_ids)

    # --------------------------------------------------
    # Session details and duration
    # --------------------------------------------------

    def test_session_history_returns_correct_session_details(self):
        now = timezone.now()
        connected_at = now - timedelta(minutes=90)
        ended_at = now - timedelta(minutes=25)

        service_request = self.create_request(
            specialisation=Specialisation.MEDICAL,
            assigned_at=now - timedelta(hours=2),
            ended_at=ended_at,
            request_status=ServiceRequest.Status.COMPLETED,
        )

        VideoSession.objects.create(
            service_request=service_request,
            status=VideoSession.Status.ENDED,
            connected_at=connected_at,
            ended_at=ended_at,
        )

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

        session = response.data[0]

        self.assertEqual(session["id"], service_request.pk)
        self.assertEqual(session["with_name"], self.deaf_user.full_name)
        self.assertEqual(session["session_type"], "Medical")
        self.assertEqual(session["duration_minutes"], 65)
        self.assertEqual(session["status"], "completed")

    def test_session_start_uses_video_connected_at(self):
        now = timezone.now()
        connected_at = now - timedelta(minutes=30)

        self.create_request(
            assigned_at=now - timedelta(hours=1),
        )

        service_request = ServiceRequest.objects.latest("id")

        VideoSession.objects.create(
            service_request=service_request,
            status=VideoSession.Status.CONNECTED,
            connected_at=connected_at,
        )

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        session = response.data[0]

        self.assertEqual(session["start"], connected_at)

    def test_session_without_video_connection_has_zero_duration(self):
        self.create_request()

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data[0]["duration_minutes"], 0)

    def test_session_uses_request_end_time_when_video_end_time_is_missing(self):
        now = timezone.now()
        connected_at = now - timedelta(minutes=50)
        request_ended_at = now - timedelta(minutes=10)

        service_request = self.create_request(
            request_status=ServiceRequest.Status.COMPLETED,
            ended_at=request_ended_at,
        )

        VideoSession.objects.create(
            service_request=service_request,
            status=VideoSession.Status.ENDED,
            connected_at=connected_at,
            ended_at=None,
        )

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        session = response.data[0]

        self.assertEqual(session["duration_minutes"], 40)

    # --------------------------------------------------
    # Session status mapping
    # --------------------------------------------------

    def test_session_statuses_are_mapped_correctly(self):
        expected_statuses = {
            ServiceRequest.Status.ASSIGNED: "upcoming",
            ServiceRequest.Status.COMPLETED: "completed",
            ServiceRequest.Status.CANCELLED: "cancelled",
            ServiceRequest.Status.FAILED: "failed",
        }

        created_requests = {}

        for request_status, expected_status in expected_statuses.items():
            service_request = self.create_request(
                request_status=request_status,
            )

            created_requests[service_request.pk] = expected_status

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        returned_statuses = {
            session["id"]: session["status"]
            for session in response.data
        }

        self.assertEqual(returned_statuses, created_requests)

    # --------------------------------------------------
    # Privacy
    # --------------------------------------------------

    def test_session_history_does_not_expose_private_call_content(self):
        service_request = self.create_request()

        VideoSession.objects.create(
            service_request=service_request,
            status=VideoSession.Status.ENDED,
            connected_at=timezone.now() - timedelta(minutes=20),
            ended_at=timezone.now(),
        )

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        session = response.data[0]

        private_fields = [
            "description",
            "transcript",
            "comment",
            "joined_participants",
            "requester",
        ]

        for field in private_fields:
            with self.subTest(field=field):
                self.assertNotIn(field, session)
