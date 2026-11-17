
"""Tests for the V2-035 personal session history endpoint."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.realtime.models import SessionFeedback, VideoSession
from apps.service_requests.services import create_request, respond_to_offer
from apps.service_requests.test_offers import make_interpreter, make_user

User = get_user_model()


class SessionHistoryEndpointTests(APITestCase):
    def setUp(self):
        self.deaf = make_user(User.Role.DEAF_HOH)
        self.interpreter = make_interpreter()
        self.other_deaf = make_user(User.Role.DEAF_HOH)

        self.video_session = self.create_completed_session(
            self.deaf,
            self.interpreter,
            description="Private conversation description",
        )

        self.url = reverse("session-history")

    def create_completed_session(self, requester, interpreter, description=""):
        """Create a completed video session for testing."""

        service_request, _ = create_request(
            requester=requester,
            description=description,
        )

        offer = service_request.offers.get(
            interpreter=interpreter,
        )
        respond_to_offer(offer, "accept")

        video_session = VideoSession.objects.get(
            service_request=service_request,
        )

        connected_at = timezone.now() - timedelta(minutes=15)
        ended_at = timezone.now()

        video_session.status = VideoSession.Status.ENDED
        video_session.connected_at = connected_at
        video_session.ended_at = ended_at
        video_session.save(
            update_fields=["status", "connected_at", "ended_at"]
        )

        return video_session

    def test_deaf_user_can_view_own_session_history(self):
        """A Deaf user can see their own completed sessions."""

        self.client.force_authenticate(self.deaf)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(
            response.data[0]["id"],
            self.video_session.pk,
        )

    def test_interpreter_can_view_assigned_session_history(self):
        """An interpreter can see sessions assigned to them."""

        self.client.force_authenticate(self.interpreter.user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(
            response.data[0]["id"],
            self.video_session.pk,
        )

    def test_deaf_user_cannot_view_another_users_sessions(self):
        """A Deaf user cannot see another user's session history."""

        self.client.force_authenticate(self.other_deaf)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_history_contains_expected_session_metadata(self):
        """The response contains date, time, duration and participants."""

        self.client.force_authenticate(self.deaf)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        session = response.data[0]

        self.assertEqual(
            session["date"],
            self.video_session.connected_at.strftime("%Y-%m-%d"),
        )
        self.assertIsNotNone(session["started_at"])
        self.assertIsNotNone(session["ended_at"])
        self.assertEqual(session["duration_minutes"], 15)

        # The backend does not yet record the actual call initiator.
        self.assertIsNone(session["initiator"])

        self.assertEqual(
            session["other_participant"]["name"],
            self.interpreter.user.full_name,
        )

    def test_interpreter_sees_requester_as_other_participant(self):
        """The interpreter sees the Deaf user as the other participant."""

        self.client.force_authenticate(self.interpreter.user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data[0]["other_participant"]["name"],
            self.deaf.full_name,
        )

    def test_unconnected_sessions_are_excluded(self):
        """Sessions without connection and end timestamps are excluded."""

        self.video_session.status = VideoSession.Status.WAITING
        self.video_session.connected_at = None
        self.video_session.ended_at = None
        self.video_session.save(
            update_fields=["status", "connected_at", "ended_at"]
        )

        self.client.force_authenticate(self.deaf)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_conversation_content_and_feedback_are_not_exposed(self):
        """Personal history must not expose private conversation content."""

        SessionFeedback.objects.create(
            video_session=self.video_session,
            interpreter=self.interpreter,
            submitted_by=self.deaf,
            rating=5,
            comment="Private feedback",
        )

        self.client.force_authenticate(self.deaf)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        session = response.data[0]

        self.assertNotIn("description", session)
        self.assertNotIn("comment", session)
        self.assertNotIn("joined_participants", session)
        self.assertNotIn("feedback", session)

    def test_institution_user_is_forbidden(self):
        """Users with other roles cannot access personal session history."""

        institution_user = make_user(User.Role.INSTITUTION)

        self.client.force_authenticate(institution_user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_user_is_rejected(self):
        """Unauthenticated users cannot access session history."""

        self.client.force_authenticate(None)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
