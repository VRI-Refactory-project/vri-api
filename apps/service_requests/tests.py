
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.interpreters.models import (
    InterpreterAvailability,
    InterpreterProfile,
    Specialisation,
    VerificationStatus,
)
from apps.notifications.models import Notification
from apps.service_requests.models import ServiceRequest

User = get_user_model()


class ServiceRequestAPITests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.requester = User.objects.create_user(
            phone_number="+256700000201",
            password="TestPassword123!",
            full_name="Test Requester",
            role=User.Role.DEAF_HOH,
        )
        cls.other_requester = User.objects.create_user(
            phone_number="+256700000202",
            password="TestPassword123!",
            full_name="Other Requester",
            role=User.Role.DEAF_HOH,
        )
        cls.interpreter_user = User.objects.create_user(
            phone_number="+256700000203",
            password="TestPassword123!",
            full_name="Test Interpreter",
            email="interpreter203@example.com",
            role=User.Role.INTERPRETER,
        )
        cls.interpreter = InterpreterProfile.objects.create(
            user=cls.interpreter_user,
            verification_status=VerificationStatus.APPROVED,
            is_available=True,
            specialisation=Specialisation.GENERAL,
        )

    def setUp(self):
        self.client.force_authenticate(user=self.requester)
        self.url = reverse("service-request-list-create")

    def make_interpreter_available(self):
        now = timezone.now()
        InterpreterAvailability.objects.create(
            interpreter=self.interpreter,
            start_at=now - timedelta(minutes=30),
            end_at=now + timedelta(hours=1),
        )

    @patch("apps.service_requests.services.send_expo_push")
    def test_request_is_assigned_and_notification_created(self, mock_push):
        self.make_interpreter_available()

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                self.url,
                {
                    "specialisation_required": Specialisation.GENERAL,
                    "description": "Please arrange a sign language interpreter.",
                },
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], ServiceRequest.Status.ASSIGNED)
        self.assertEqual(
            response.data["interpreter_id"],
            self.interpreter.pk,
        )

        service_request = ServiceRequest.objects.get(pk=response.data["id"])
        self.assertEqual(service_request.requester, self.requester)
        self.assertEqual(service_request.interpreter, self.interpreter)
        self.assertIsNotNone(service_request.assigned_at)

        notification = Notification.objects.get(
            user=self.interpreter_user,
            category=Notification.Category.INCOMING_REQUEST,
        )
        self.assertEqual(notification.data["request_id"], service_request.pk)
        self.assertEqual(notification.data["screen"], "request-details")
        mock_push.assert_called_once_with(notification)


    def test_request_remains_pending_when_no_interpreter_is_available(self):
        self.interpreter.is_available = False
        self.interpreter.save(update_fields=["is_available"])

        response = self.client.post(
            self.url,
            {
                "specialisation_required": Specialisation.GENERAL,
                "description": "Waiting for an available interpreter.",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], ServiceRequest.Status.PENDING)
        self.assertIsNone(response.data["interpreter_id"])
        self.assertFalse(
            Notification.objects.filter(
                user=self.interpreter_user,
                category=Notification.Category.INCOMING_REQUEST,
            ).exists()
        )


    def test_interpreter_cannot_create_service_request(self):
        self.client.force_authenticate(user=self.interpreter_user)

        response = self.client.post(
            self.url,
            {"specialisation_required": Specialisation.GENERAL},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_request_creation_requires_authentication(self):
        self.client.force_authenticate(user=None)

        response = self.client.post(
            self.url,
            {"specialisation_required": Specialisation.GENERAL},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_requester_only_sees_their_own_requests(self):
        own_request = ServiceRequest.objects.create(
            requester=self.requester,
            specialisation_required=Specialisation.GENERAL,
        )
        ServiceRequest.objects.create(
            requester=self.other_requester,
            specialisation_required=Specialisation.GENERAL,
        )

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["id"], own_request.pk)

    def test_invalid_specialisation_is_rejected(self):
        response = self.client.post(
            self.url,
            {"specialisation_required": "INVALID"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


    def test_client_cannot_choose_interpreter_or_set_status(self):
        self.interpreter.is_available = False
        self.interpreter.save(update_fields=["is_available"])

        response = self.client.post(
            self.url,
            {
                "specialisation_required": Specialisation.GENERAL,
                "interpreter_id": self.interpreter.pk,
                "status": ServiceRequest.Status.ASSIGNED,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], ServiceRequest.Status.PENDING)
        self.assertIsNone(response.data["interpreter_id"])
