"""The sessions an interpreter took, for the dashboard."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.service_requests.models import ServiceRequest
from apps.service_requests.test_offers import make_interpreter, make_user

User = get_user_model()
Status = ServiceRequest.Status


class InterpreterSessionListTests(APITestCase):
    def setUp(self):
        self.interpreter = make_interpreter()
        self.deaf = make_user(User.Role.DEAF_HOH)
        self.url = reverse("interpreter-session-list")
        self.client.force_authenticate(self.interpreter.user)

    def make_session(
        self, *, interpreter, requester, session_status, started, minutes=None
    ):
        return ServiceRequest.objects.create(
            requester=requester,
            interpreter=interpreter,
            status=session_status,
            assigned_at=started,
            ended_at=started + timedelta(minutes=minutes) if minutes else None,
        )

    def test_lists_running_and_finished_sessions_newest_first(self):
        now = timezone.now()
        finished = self.make_session(
            interpreter=self.interpreter,
            requester=self.deaf,
            session_status=Status.COMPLETED,
            started=now - timedelta(days=1),
            minutes=30,
        )
        running = self.make_session(
            interpreter=self.interpreter,
            requester=make_user(User.Role.DEAF_HOH),
            session_status=Status.ASSIGNED,
            started=now - timedelta(minutes=5),
        )

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [item["id"] for item in response.data], [running.pk, finished.pk]
        )
        self.assertEqual(response.data[0]["status"], "ASSIGNED")
        self.assertIsNone(response.data[0]["duration_minutes"])
        self.assertEqual(response.data[1]["status"], "COMPLETED")
        self.assertEqual(response.data[1]["duration_minutes"], 30)
        self.assertEqual(response.data[1]["requester_name"], self.deaf.full_name)
        self.assertIsNone(response.data[1]["requester_photo"])

    def test_leaves_out_other_interpreters_and_requests_nobody_took(self):
        now = timezone.now()
        self.make_session(
            interpreter=make_interpreter(),
            requester=self.deaf,
            session_status=Status.ASSIGNED,
            started=now,
        )
        self.make_session(
            interpreter=self.interpreter,
            requester=make_user(User.Role.DEAF_HOH),
            session_status=Status.CANCELLED,
            started=now,
        )

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_only_interpreters_can_ask(self):
        self.client.force_authenticate(self.deaf)

        self.assertEqual(
            self.client.get(self.url).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_needs_a_signed_in_user(self):
        self.client.force_authenticate(None)

        self.assertEqual(
            self.client.get(self.url).status_code, status.HTTP_401_UNAUTHORIZED
        )
