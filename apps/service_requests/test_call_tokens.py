"""V2-011: only the people on an accepted session can get a token for its call."""

import jwt
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.service_requests.models import ServiceRequest
from apps.service_requests.services import create_request, respond_to_offer
from apps.service_requests.test_offers import make_interpreter, make_user

User = get_user_model()
Status = ServiceRequest.Status

TEST_SECRET = "test-secret-that-is-at-least-32-characters"


@override_settings(
    LIVEKIT_URL="ws://livekit.test:7880",
    LIVEKIT_API_KEY="testkey",
    LIVEKIT_API_SECRET=TEST_SECRET,
)
class CallTokenTests(APITestCase):
    def setUp(self):
        self.interpreter = make_interpreter()
        self.deaf = make_user(User.Role.DEAF_HOH)
        self.service_request, _ = create_request(requester=self.deaf)
        self.offer = self.service_request.offers.get()

    def accept(self):
        respond_to_offer(self.offer, "accept")
        self.service_request.refresh_from_db()

    def ask(self, user, service_request=None):
        self.client.force_authenticate(user)
        url = reverse(
            "service-request-call-token",
            kwargs={"pk": (service_request or self.service_request).pk},
        )
        return self.client.post(url)

    def claims(self, response):
        return jwt.decode(response.data["token"], TEST_SECRET, algorithms=["HS256"])

    # --- Who gets in ---

    def test_the_requester_gets_a_token_for_the_session_room(self):
        self.accept()

        response = self.ask(self.deaf)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        room = f"session-{self.service_request.pk}"
        self.assertEqual(response.data["room"], room)
        self.assertEqual(response.data["url"], "ws://livekit.test:7880")
        claims = self.claims(response)
        self.assertEqual(claims["sub"], f"user-{self.deaf.pk}")
        self.assertEqual(claims["iss"], "testkey")
        self.assertEqual(claims["video"]["room"], room)
        self.assertTrue(claims["video"]["roomJoin"])

    def test_the_assigned_interpreter_gets_a_token(self):
        self.accept()

        response = self.ask(self.interpreter.user)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.claims(response)["sub"], f"user-{self.interpreter.user.pk}")

    # --- Who stays out ---

    def test_another_deaf_user_gets_404(self):
        self.accept()

        response = self.ask(make_user(User.Role.DEAF_HOH))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_another_interpreter_gets_404(self):
        self.accept()

        response = self.ask(make_interpreter().user)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_signing_in_is_required(self):
        self.accept()

        response = self.ask(None)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    # --- Only an accepted session can be joined ---

    def test_an_interpreter_who_was_only_offered_it_gets_409(self):
        self.assertEqual(self.service_request.status, Status.OFFERED)

        response = self.ask(self.interpreter.user)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["code"], "session_not_ready")
        self.assertNotIn("token", response.data)

    def test_a_cancelled_session_cannot_be_joined(self):
        self.accept()
        ServiceRequest.objects.filter(pk=self.service_request.pk).update(status=Status.CANCELLED)

        response = self.ask(self.deaf)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
