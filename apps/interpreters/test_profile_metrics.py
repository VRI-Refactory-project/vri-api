from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.realtime.models import SessionFeedback, VideoSession
from apps.service_requests.models import ServiceRequest
from apps.service_requests.test_offers import make_interpreter, make_user

User = get_user_model()


class InterpreterProfileMetricsTests(APITestCase):
    def setUp(self):
        self.interpreter = make_interpreter()
        self.deaf = make_user(User.Role.DEAF_HOH)
        self.other_interpreter = make_interpreter()
        self.url = reverse("interpreters:interpreter-profile-metrics")
        self.client.force_authenticate(self.interpreter.user)

    def add_session(self, state, *, interpreter=None, requester=None, feedback=None):
        request = ServiceRequest.objects.create(
            requester=requester or self.deaf,
            interpreter=interpreter or self.interpreter,
            status=state,
        )
        if feedback is not None:
            video = VideoSession.objects.create(
                service_request=request, status=VideoSession.Status.ENDED
            )
            SessionFeedback.objects.create(
                video_session=video,
                interpreter=request.interpreter,
                submitted_by=request.requester,
                rating=feedback,
            )
        return request

    def test_empty_profile_returns_null_rating_and_zero_sessions(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data, {"average_rating": None, "completed_sessions": 0}
        )

    def test_aggregates_requester_ratings_and_only_completed_assigned_sessions(self):
        self.add_session(ServiceRequest.Status.COMPLETED, feedback=5)
        self.add_session(ServiceRequest.Status.COMPLETED, feedback=4)
        for state in (
            ServiceRequest.Status.ASSIGNED,
            ServiceRequest.Status.CANCELLED,
            ServiceRequest.Status.FAILED,
        ):
            self.add_session(state)
        self.add_session(
            ServiceRequest.Status.COMPLETED,
            interpreter=self.other_interpreter,
            feedback=1,
        )

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data, {"average_rating": 4.5, "completed_sessions": 2}
        )

    def test_only_interpreters_can_read_metrics(self):
        self.client.force_authenticate(self.deaf)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_authentication_is_required(self):
        self.client.force_authenticate(None)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
