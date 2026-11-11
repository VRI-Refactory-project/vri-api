"""V2-034: only the person who started an accepted session can end it for everyone."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.realtime.models import VideoSession
from apps.service_requests.models import ServiceRequest, SessionInvitation
from apps.service_requests.services import (
    InvalidTransition,
    check_transition,
    create_request,
    respond_to_offer,
)
from apps.service_requests.test_offers import make_interpreter, make_user

User = get_user_model()
Status = ServiceRequest.Status


class EndSessionTests(APITestCase):
    def setUp(self):
        self.interpreter = make_interpreter()
        self.deaf = make_user(User.Role.DEAF_HOH)
        self.service_request, _ = create_request(requester=self.deaf)
        self.offer = self.service_request.offers.get()

        patcher = mock.patch("apps.service_requests.services.end_room")
        self.end_room = patcher.start()
        self.addCleanup(patcher.stop)

        patcher = mock.patch("apps.service_requests.status_broadcast.send_to_session")
        self.send = patcher.start()
        self.addCleanup(patcher.stop)

    def accept(self):
        respond_to_offer(self.offer, "accept")
        self.service_request.refresh_from_db()

    def end(self, user, service_request=None):
        self.client.force_authenticate(user)
        url = reverse(
            "service-request-end",
            kwargs={"pk": (service_request or self.service_request).pk},
        )
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(url)

    # --- Who can end it ---

    def test_the_requester_ends_the_session(self):
        self.accept()

        response = self.end(self.deaf)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "COMPLETED")
        self.service_request.refresh_from_db()
        self.assertEqual(self.service_request.status, Status.COMPLETED)
        self.assertIsNotNone(self.service_request.ended_at)

    def test_the_assigned_interpreter_cannot_end_it_for_everyone(self):
        self.accept()

        response = self.end(self.interpreter.user)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["code"], "only_starter_can_end")
        self.service_request.refresh_from_db()
        self.assertEqual(self.service_request.status, Status.ASSIGNED)
        self.end_room.assert_not_called()

    def test_someone_who_is_not_on_the_session_gets_404(self):
        self.accept()
        stranger = make_user(User.Role.DEAF_HOH)

        response = self.end(stranger)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.service_request.refresh_from_db()
        self.assertEqual(self.service_request.status, Status.ASSIGNED)

    def test_signing_in_is_required(self):
        self.accept()
        url = reverse("service-request-end", kwargs={"pk": self.service_request.pk})

        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    # --- What it does ---

    def test_everyone_on_the_session_is_told_and_the_call_is_closed(self):
        self.accept()
        self.send.reset_mock()

        self.end(self.deaf)

        self.send.assert_called_once_with(
            self.service_request.pk,
            "session.status",
            {"status": "COMPLETED", "label": "Session completed"},
        )
        self.end_room.assert_called_once()
        self.assertEqual(self.end_room.call_args.args[0].pk, self.service_request.pk)

    def test_the_video_session_is_marked_ended(self):
        self.accept()
        # Accepting the request already starts the video session.
        video_session = VideoSession.objects.get(service_request=self.service_request)
        self.assertNotEqual(video_session.status, VideoSession.Status.ENDED)

        self.end(self.deaf)

        video_session.refresh_from_db()
        self.assertEqual(video_session.status, VideoSession.Status.ENDED)
        self.assertIsNotNone(video_session.ended_at)

    def test_guest_links_stop_working(self):
        self.accept()
        invitation = SessionInvitation.objects.create(
            service_request=self.service_request, email="guest@example.com"
        )
        self.assertTrue(invitation.is_open)

        self.end(self.deaf)

        invitation.refresh_from_db()
        invitation.service_request.refresh_from_db()
        self.assertFalse(invitation.is_open)

    def test_ending_twice_changes_nothing_the_second_time(self):
        self.accept()
        self.end(self.deaf)
        self.service_request.refresh_from_db()
        first_end = self.service_request.ended_at

        response = self.end(self.deaf)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.service_request.refresh_from_db()
        self.assertEqual(self.service_request.ended_at, first_end)
        self.end_room.assert_called_once()

    # --- When it cannot be ended ---

    def test_a_session_nobody_accepted_cannot_be_ended(self):
        response = self.end(self.deaf)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["code"], "not_endable")
        self.end_room.assert_not_called()

    def test_a_cancelled_request_cannot_be_ended(self):
        self.client.force_authenticate(self.deaf)
        self.client.post(
            reverse("service-request-cancel", kwargs={"pk": self.service_request.pk})
        )

        response = self.end(self.deaf)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_a_completed_session_cannot_start_again(self):
        with self.assertRaises(InvalidTransition):
            check_transition(Status.COMPLETED, Status.ASSIGNED)
