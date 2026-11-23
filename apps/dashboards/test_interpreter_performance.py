from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.realtime.models import SessionFeedback, VideoSession
from apps.service_requests.models import ServiceRequest
from apps.service_requests.test_offers import make_interpreter, make_user

User = get_user_model()


class AdminInterpreterPerformanceTests(APITestCase):
    def setUp(self):
        self.interpreter = make_interpreter()
        self.deaf = make_user(User.Role.DEAF_HOH)
        self.admin = make_user(User.Role.ADMIN)
        self.url = reverse("dashboard-admin-interpreter-performance")

    def add_feedback(self, rating, *, when=None, comment="", interpreter=None):
        interpreter = interpreter or self.interpreter
        ended_at = when or timezone.now()
        service_request = ServiceRequest.objects.create(
            requester=self.deaf,
            interpreter=interpreter,
            status=ServiceRequest.Status.COMPLETED,
            ended_at=ended_at,
        )
        video = VideoSession.objects.create(
            service_request=service_request,
            status=VideoSession.Status.ENDED,
            ended_at=ended_at,
        )
        feedback = SessionFeedback.objects.create(
            video_session=video,
            interpreter=interpreter,
            submitted_by=self.deaf,
            rating=rating,
            comment=comment,
        )
        return service_request, video, feedback

    def test_requires_admin_role_and_authentication(self):
        self.assertEqual(
            self.client.get(self.url).status_code, status.HTTP_401_UNAUTHORIZED
        )
        self.client.force_authenticate(self.deaf)
        self.assertEqual(
            self.client.get(self.url).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_empty_aggregates_and_chronological_twelve_month_trends(self):
        self.client.force_authenticate(self.admin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["summary"],
            {
                "average_rating": None,
                "ratings_count": 0,
                "low_rating_count": 0,
                "completed_sessions": 0,
            },
        )
        self.assertEqual(len(response.data["trends"]), 12)
        self.assertEqual(
            response.data["trends"],
            sorted(response.data["trends"], key=lambda row: row["month"]),
        )

    def test_aggregates_only_deaf_ratings_and_completed_ended_sessions(self):
        self.client.force_authenticate(self.admin)
        recent_month = timezone.now() - timedelta(days=10)
        self.add_feedback(5, when=recent_month, comment="Great")
        _request, _video, low = self.add_feedback(
            2, when=recent_month, comment="Needs work"
        )
        # A completed request whose call failed does not count as a completed session or rating.
        _failed_request, failed_video, _ = self.add_feedback(1)
        failed_video.status = VideoSession.Status.FAILED
        failed_video.save(update_fields=["status"])
        # Interpreter-submitted feedback is not part of performance ratings.
        _other_request, other_video, _ = self.add_feedback(1)
        other_video.feedback.filter(submitted_by=self.deaf).delete()
        SessionFeedback.objects.create(
            video_session=other_video,
            interpreter=self.interpreter,
            submitted_by=self.interpreter.user,
            rating=1,
        )
        # Active sessions are excluded from completed session counts.
        ServiceRequest.objects.create(
            requester=make_user(User.Role.DEAF_HOH),
            interpreter=self.interpreter,
            status=ServiceRequest.Status.ASSIGNED,
        )

        response = self.client.get(self.url)

        self.assertEqual(
            response.data["summary"],
            {
                "average_rating": 3.5,
                "ratings_count": 2,
                "low_rating_count": 1,
                "completed_sessions": 3,
            },
        )
        current_key = timezone.localtime().strftime("%Y-%m")
        trend = next(
            row for row in response.data["trends"] if row["month"] == current_key
        )
        self.assertEqual(
            trend, {"month": current_key, "average_rating": 3.5, "rating_count": 2}
        )
        self.assertEqual(response.data["low_ratings"][0]["id"], low.pk)
        self.assertEqual(response.data["low_ratings"][0]["comment"], "Needs work")
        self.assertNotIn("requester", response.data["low_ratings"][0])
        interpreter_row = next(
            row
            for row in response.data["interpreters"]
            if row["interpreter_id"] == self.interpreter.pk
        )
        self.assertEqual(interpreter_row["average_rating"], 3.5)
        self.assertEqual(interpreter_row["completed_sessions"], 3)

    def test_low_ratings_are_recent_first_and_limited(self):
        self.client.force_authenticate(self.admin)
        for index in range(26):
            self.add_feedback(1, when=timezone.now() - timedelta(days=26 - index))

        response = self.client.get(self.url)

        low_rows = response.data["low_ratings"]
        self.assertEqual(len(low_rows), 25)
        self.assertEqual(
            low_rows[0]["id"],
            SessionFeedback.objects.order_by(
                "-video_session__service_request__ended_at", "-pk"
            )
            .first()
            .pk,
        )
