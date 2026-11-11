"""Tests for what the apps show for a request's call status (V2-011, V2-033)."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.realtime.call_status import display_status
from apps.realtime.models import VideoSession
from apps.service_requests.models import ServiceRequest
from apps.service_requests.services import create_request, respond_to_offer
from apps.service_requests.test_offers import make_interpreter, make_user

User = get_user_model()


class DisplayStatusTests(TestCase):
    def setUp(self):
        interpreter = make_interpreter()
        requester = make_user(User.Role.DEAF_HOH)
        self.service_request, _ = create_request(requester=requester)
        respond_to_offer(self.service_request.offers.get(), "accept")
        self.video_session = VideoSession.objects.get(service_request=self.service_request)
        self.assertIsNotNone(interpreter)

    def set_states(self, request_status, call_status):
        ServiceRequest.objects.filter(pk=self.service_request.pk).update(status=request_status)
        VideoSession.objects.filter(pk=self.video_session.pk).update(status=call_status)
        self.service_request.refresh_from_db()
        return display_status(self.service_request)

    def test_assigned_request_follows_the_call(self):
        cases = {
            VideoSession.Status.WAITING: ServiceRequest.Status.ASSIGNED,
            VideoSession.Status.CONNECTING: "CONNECTING",
            VideoSession.Status.CONNECTED: "ACTIVE",
            VideoSession.Status.ENDED: ServiceRequest.Status.COMPLETED,
            VideoSession.Status.FAILED: ServiceRequest.Status.FAILED,
        }
        for call_status, shown in cases.items():
            with self.subTest(call_status=call_status):
                self.assertEqual(self.set_states(ServiceRequest.Status.ASSIGNED, call_status), shown)

    def test_failed_call_shows_as_failed_even_though_the_request_is_completed(self):
        # The V2-033 outcome for a call that never connected: request COMPLETED, video FAILED.
        self.assertEqual(
            self.set_states(ServiceRequest.Status.COMPLETED, VideoSession.Status.FAILED),
            ServiceRequest.Status.FAILED,
        )

    def test_ended_call_on_a_completed_request_shows_completed(self):
        self.assertEqual(
            self.set_states(ServiceRequest.Status.COMPLETED, VideoSession.Status.ENDED),
            ServiceRequest.Status.COMPLETED,
        )

    def test_cancelled_request_keeps_its_own_status(self):
        self.assertEqual(
            self.set_states(ServiceRequest.Status.CANCELLED, VideoSession.Status.WAITING),
            ServiceRequest.Status.CANCELLED,
        )

    def test_request_without_a_call_keeps_its_own_status(self):
        VideoSession.objects.filter(pk=self.video_session.pk).delete()
        self.service_request.refresh_from_db()
        self.assertEqual(display_status(self.service_request), ServiceRequest.Status.ASSIGNED)
