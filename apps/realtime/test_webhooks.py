

"""Tests for the LiveKit webhook endpoint."""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase


class LiveKitWebhookTests(APITestCase):
    def setUp(self):
        self.url = reverse("livekit-webhook")
        self.body = '{"event":"room_finished"}'
        self.authorization = "Bearer test-livekit-token"

    @override_settings(LIVEKIT_API_KEY="", LIVEKIT_API_SECRET="")
    def test_missing_livekit_credentials_returns_service_unavailable(self):
        response = self.client.generic(
            "POST",
            self.url,
            self.body,
            content_type="application/json",
            HTTP_AUTHORIZATION=self.authorization,
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    @override_settings(
        LIVEKIT_API_KEY="test-api-key",
        LIVEKIT_API_SECRET="test-api-secret",
    )
    def test_missing_authorization_header_returns_unauthorized(self):
        response = self.client.generic(
            "POST",
            self.url,
            self.body,
            content_type="application/json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    @override_settings(
        LIVEKIT_API_KEY="test-api-key",
        LIVEKIT_API_SECRET="test-api-secret",
    )
    @patch("apps.realtime.views.api.WebhookReceiver")
    def test_invalid_webhook_signature_returns_unauthorized(
        self,
        mock_receiver,
    ):
        mock_receiver.return_value.receive.side_effect = ValueError(
            "Invalid signature"
        )

        response = self.client.generic(
            "POST",
            self.url,
            self.body,
            content_type="application/json",
            HTTP_AUTHORIZATION=self.authorization,
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_401_UNAUTHORIZED,
        )
        mock_receiver.return_value.receive.assert_called_once_with(
            self.body,
            self.authorization,
        )

    @override_settings(
        LIVEKIT_API_KEY="test-api-key",
        LIVEKIT_API_SECRET="test-api-secret",
    )
    @patch("apps.realtime.views.process_livekit_event")
    @patch("apps.realtime.views.api.WebhookReceiver")
    def test_valid_webhook_processes_event_and_returns_success(
        self,
        mock_receiver,
        mock_process_event,
    ):
        event = SimpleNamespace(event="room_finished")
        mock_receiver.return_value.receive.return_value = event

        response = self.client.generic(
            "POST",
            self.url,
            self.body,
            content_type="application/json",
            HTTP_AUTHORIZATION=self.authorization,
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        mock_receiver.return_value.receive.assert_called_once_with(
            self.body,
            self.authorization,
        )
        mock_process_event.assert_called_once_with(event)
