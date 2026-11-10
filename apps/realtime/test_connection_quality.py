"""Tests for the V2-061 connection-quality assessment endpoints."""

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.service_requests.models import SessionInvitation
from apps.service_requests.services import create_request, respond_to_offer
from apps.service_requests.test_offers import make_interpreter, make_user

User = get_user_model()


class ConnectionQualityTestEndpointTests(APITestCase):
    def setUp(self):
        self.user = make_user(User.Role.DEAF_HOH)
        self.client.force_authenticate(self.user)
        self.url = reverse("connection-quality-test")

        self.good_metrics = {
            "latency_ms": 150,
            "jitter_ms": 15,
            "packet_loss_percent": 1,
            "upload_bandwidth_kbps": 800,
            "download_bandwidth_kbps": 1500,
        }

    def test_good_connection_passes(self):
        response = self.client.post(
            self.url,
            self.good_metrics,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["passed"])
        self.assertEqual(response.data["quality"], "good")
        self.assertEqual(response.data["failed_metrics"], [])
        self.assertEqual(response.data["metrics"], self.good_metrics)

    def test_connection_below_all_thresholds_fails(self):
        metrics = {
            "latency_ms": 350,
            "jitter_ms": 35,
            "packet_loss_percent": 4,
            "upload_bandwidth_kbps": 400,
            "download_bandwidth_kbps": 900,
        }

        response = self.client.post(self.url, metrics, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["passed"])
        self.assertEqual(response.data["quality"], "below_minimum")
        self.assertEqual(
            response.data["failed_metrics"],
            [
                "latency_ms",
                "jitter_ms",
                "packet_loss_percent",
                "upload_bandwidth_kbps",
                "download_bandwidth_kbps",
            ],
        )

    def test_missing_metric_is_rejected(self):
        metrics = self.good_metrics.copy()
        del metrics["latency_ms"]

        response = self.client.post(self.url, metrics, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("latency_ms", response.data)

    def test_negative_metric_is_rejected(self):
        metrics = self.good_metrics.copy()
        metrics["latency_ms"] = -1

        response = self.client.post(self.url, metrics, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_packet_loss_above_100_percent_is_rejected(self):
        metrics = self.good_metrics.copy()
        metrics["packet_loss_percent"] = 101

        response = self.client.post(self.url, metrics, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_authentication_is_required(self):
        self.client.force_authenticate(None)

        response = self.client.post(
            self.url,
            self.good_metrics,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class GuestConnectionQualityTestEndpointTests(APITestCase):
    def setUp(self):
        self.interpreter = make_interpreter()
        self.deaf = make_user(User.Role.DEAF_HOH)
        self.service_request, _ = create_request(requester=self.deaf)

        offer = self.service_request.offers.get()
        respond_to_offer(offer, "accept")

        self.invitation = SessionInvitation.objects.create(
            service_request=self.service_request,
            email="guest@example.com",
        )

        self.url = reverse(
            "guest-connection-quality-test",
            kwargs={"token": self.invitation.token},
        )

        self.good_metrics = {
            "latency_ms": 150,
            "jitter_ms": 15,
            "packet_loss_percent": 1,
            "upload_bandwidth_kbps": 800,
            "download_bandwidth_kbps": 1500,
        }

        self.client.force_authenticate(None)

    def test_guest_with_open_invitation_can_test_connection(self):
        response = self.client.post(
            self.url,
            self.good_metrics,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["passed"])

    def test_invalid_guest_token_is_rejected(self):
        url = reverse(
            "guest-connection-quality-test",
            kwargs={"token": "invalid-token"},
        )

        response = self.client.post(
            url,
            self.good_metrics,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data["code"], "invalid_link")

    def test_removed_guest_cannot_test_connection(self):
        self.invitation.removed_at = timezone.now()
        self.invitation.save(update_fields=["removed_at"])

        response = self.client.post(
            self.url,
            self.good_metrics,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_410_GONE)
        self.assertEqual(response.data["code"], "link_closed")
