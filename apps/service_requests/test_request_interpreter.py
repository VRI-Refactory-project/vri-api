"""Which requests say who their interpreter was (the "No interpreter" bug on finished sessions)."""

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.service_requests.models import ServiceRequest
from apps.service_requests.test_offers import make_interpreter, make_user

User = get_user_model()
Status = ServiceRequest.Status


class RequestInterpreterTests(APITestCase):
    def setUp(self):
        self.requester = make_user(User.Role.DEAF_HOH)
        self.interpreter = make_interpreter()
        self.client.force_authenticate(self.requester)

    def request_with(self, status):
        return ServiceRequest.objects.create(
            requester=self.requester, interpreter=self.interpreter, status=status
        )

    def shown(self, service_request):
        response = self.client.get(reverse("service-request-detail", args=[service_request.pk]))
        self.assertEqual(response.status_code, 200)
        return response.data["interpreter_id"], response.data["interpreter"]

    def test_a_completed_session_still_says_who_interpreted_it(self):
        interpreter_id, interpreter = self.shown(self.request_with(Status.COMPLETED))

        self.assertEqual(interpreter_id, self.interpreter.pk)
        self.assertEqual(interpreter["name"], self.interpreter.user.full_name)

    def test_a_running_session_says_who_is_interpreting(self):
        interpreter_id, interpreter = self.shown(self.request_with(Status.ASSIGNED))

        self.assertEqual(interpreter_id, self.interpreter.pk)
        self.assertEqual(interpreter["name"], self.interpreter.user.full_name)

    def test_a_cancelled_or_failed_request_names_nobody(self):
        for status in (Status.CANCELLED, Status.FAILED):
            with self.subTest(status=status):
                self.assertEqual(self.shown(self.request_with(status)), (None, None))

    def test_the_session_list_shows_it_too(self):
        completed = self.request_with(Status.COMPLETED)

        response = self.client.get(reverse("service-request-list-create"))

        rows = response.data["results"] if isinstance(response.data, dict) else response.data
        row = next(r for r in rows if r["id"] == completed.pk)
        self.assertEqual(row["interpreter"]["name"], self.interpreter.user.full_name)
