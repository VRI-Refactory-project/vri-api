
"""Tests for the V2-041 session feedback endpoint."""

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.realtime.models import SessionFeedback, VideoSession
from apps.service_requests.models import ServiceRequest
from apps.service_requests.services import create_request, respond_to_offer
from apps.service_requests.test_offers import make_interpreter, make_user

User = get_user_model()


class SessionFeedbackEndpointTests(APITestCase):
    def setUp(self):
        self.deaf = make_user(User.Role.DEAF_HOH)
        self.interpreter = make_interpreter()

        self.service_request, _ = create_request(requester=self.deaf)
        offer = self.service_request.offers.get()
        respond_to_offer(offer, "accept")

        self.service_request.refresh_from_db()
        self.service_request.status = ServiceRequest.Status.COMPLETED
        self.service_request.save(update_fields=["status"])

        self.video_session = VideoSession.objects.get(
            service_request=self.service_request,
        )
        self.video_session.status = VideoSession.Status.ENDED
        self.video_session.save(update_fields=["status"])

        self.url = reverse(
            "session-feedback",
            kwargs={"session_id": self.video_session.pk},
        )

        self.client.force_authenticate(self.deaf)

        self.payload = {
            "rating": 5,
            "accuracy_comment": "The interpretation was accurate.",
            "clarity_comment": "Communication was clear.",
        }

    def test_requester_can_submit_feedback_for_completed_session(self):
        response = self.client.post(
            self.url,
            self.payload,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["rating"], 5)
        self.assertEqual(
            response.data["accuracy_comment"],
            self.payload["accuracy_comment"],
        )
        self.assertEqual(
            response.data["clarity_comment"],
            self.payload["clarity_comment"],
        )

        feedback = SessionFeedback.objects.get(
            video_session=self.video_session,
        )
        self.assertEqual(feedback.interpreter, self.service_request.interpreter)
        self.assertEqual(feedback.submitted_by, self.deaf)

    def test_feedback_comments_are_optional(self):
        response = self.client.post(
            self.url,
            {"rating": 4},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        feedback = SessionFeedback.objects.get(
            video_session=self.video_session,
        )
        self.assertEqual(feedback.rating, 4)
        self.assertEqual(feedback.accuracy_comment, "")
        self.assertEqual(feedback.clarity_comment, "")

    def test_rating_below_one_is_rejected(self):
        response = self.client.post(
            self.url,
            {"rating": 0},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("rating", response.data)

    def test_rating_above_five_is_rejected(self):
        response = self.client.post(
            self.url,
            {"rating": 6},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("rating", response.data)

    def test_missing_rating_is_rejected(self):
        response = self.client.post(
            self.url,
            {"accuracy_comment": "Good interpretation."},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("rating", response.data)

    def test_another_user_cannot_submit_feedback(self):
        another_user = make_user(User.Role.DEAF_HOH)
        self.client.force_authenticate(another_user)

        response = self.client.post(
            self.url,
            self.payload,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(SessionFeedback.objects.count(), 0)

    def test_authentication_is_required(self):
        self.client.force_authenticate(None)

        response = self.client.post(
            self.url,
            self.payload,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_feedback_is_rejected_before_service_request_is_completed(self):
        self.service_request.status = ServiceRequest.Status.ASSIGNED
        self.service_request.save(update_fields=["status"])

        response = self.client.post(
            self.url,
            self.payload,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(SessionFeedback.objects.count(), 0)

    def test_feedback_is_rejected_before_video_session_has_ended(self):
        self.video_session.status = VideoSession.Status.CONNECTED
        self.video_session.save(update_fields=["status"])

        response = self.client.post(
            self.url,
            self.payload,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(SessionFeedback.objects.count(), 0)

    def test_feedback_is_rejected_when_session_has_no_interpreter(self):
        self.service_request.interpreter = None
        self.service_request.save(update_fields=["interpreter"])

        response = self.client.post(
            self.url,
            self.payload,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(SessionFeedback.objects.count(), 0)

    def test_feedback_can_only_be_submitted_once_per_session(self):
        first_response = self.client.post(
            self.url,
            self.payload,
            format="json",
        )

        second_response = self.client.post(
            self.url,
            {"rating": 3},
            format="json",
        )

        self.assertEqual(first_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second_response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(SessionFeedback.objects.count(), 1)

    def test_unknown_video_session_returns_not_found(self):
        url = reverse(
            "session-feedback",
            kwargs={"session_id": 999999},
        )

        response = self.client.post(
            url,
            self.payload,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_client_cannot_choose_the_interpreter_or_submitter(self):
        another_interpreter = make_interpreter()
        another_user = make_user(User.Role.DEAF_HOH)

        payload = {
            **self.payload,
            "interpreter": another_interpreter.pk,
            "submitted_by": another_user.pk,
        }

        response = self.client.post(
            self.url,
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        feedback = SessionFeedback.objects.get(
            video_session=self.video_session,
        )
        self.assertEqual(feedback.interpreter, self.service_request.interpreter)
        self.assertEqual(feedback.submitted_by, self.deaf)

    def test_interpreter_can_submit_feedback_too(self):
        interpreter_user = self.service_request.interpreter.user
        self.client.force_authenticate(interpreter_user)

        response = self.client.post(self.url, self.payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        feedback = SessionFeedback.objects.get(video_session=self.video_session)
        self.assertEqual(feedback.submitted_by, interpreter_user)
        self.assertEqual(feedback.interpreter, self.service_request.interpreter)

    def test_requester_and_interpreter_each_get_one_feedback(self):
        interpreter_user = self.service_request.interpreter.user

        first = self.client.post(self.url, self.payload, format="json")
        self.client.force_authenticate(interpreter_user)
        second = self.client.post(self.url, {"rating": 4}, format="json")
        again = self.client.post(self.url, {"rating": 2}, format="json")

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_201_CREATED)
        self.assertEqual(again.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(SessionFeedback.objects.count(), 2)

    def test_an_interpreter_from_another_session_cannot_submit(self):
        outsider = make_interpreter().user
        self.client.force_authenticate(outsider)

        response = self.client.post(self.url, self.payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(SessionFeedback.objects.count(), 0)

    def test_wizard_details_are_saved(self):
        response = self.client.post(
            self.url,
            {
                "rating": 2,
                "category": "Audio",
                "issues": ["Sound was delayed", "There was echo"],
                "comment": "Kept cutting out.",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        feedback = SessionFeedback.objects.get(video_session=self.video_session)
        self.assertEqual(feedback.category, "Audio")
        self.assertEqual(feedback.issues, ["Sound was delayed", "There was echo"])
        self.assertEqual(feedback.comment, "Kept cutting out.")

    def test_an_unknown_category_is_rejected(self):
        response = self.client.post(
            self.url, {"rating": 2, "category": "Smell"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("category", response.data)

    def test_feedback_can_be_sent_by_request_id(self):
        url = reverse(
            "request-feedback",
            kwargs={"request_id": self.service_request.pk},
        )

        response = self.client.post(url, {"rating": 5}, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            SessionFeedback.objects.get().video_session, self.video_session
        )

    def test_unknown_request_id_returns_not_found(self):
        url = reverse("request-feedback", kwargs={"request_id": 999999})

        response = self.client.post(url, {"rating": 5}, format="json")

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
