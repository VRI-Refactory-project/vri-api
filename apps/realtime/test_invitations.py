
"""Tests for secure guest invitations to VRI video sessions."""

import hashlib
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.test import APITestCase

from apps.realtime.models import GuestInvitation, VideoSession
from apps.service_requests.models import ServiceRequest
from apps.service_requests.test_offers import make_interpreter, make_user

User = get_user_model()


@override_settings(
    LIVEKIT_API_KEY="test-api-key",
    LIVEKIT_API_SECRET="test-api-secret",
    FRONTEND_URL="http://localhost:5173",
    GUEST_INVITATION_EXPIRY_SECONDS=3600,
)
class GuestInvitationEndpointTests(APITestCase):
    def setUp(self):
        self.requester = make_user(User.Role.DEAF_HOH)
        self.interpreter = make_interpreter()

        self.service_request = ServiceRequest.objects.create(
            requester=self.requester,
            interpreter=self.interpreter,
            status=ServiceRequest.Status.ASSIGNED,
        )

        self.url = reverse(
            "guest-invitation-create",
            kwargs={"session_id": self.service_request.pk},
        )

        self.client.force_authenticate(self.requester)

    def create_invitation_token(self):
        """Create an invitation and extract its raw token for guest tests."""

        response = self.client.post(
            self.url,
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        return response.data["invitation_url"].rsplit("/", 1)[-1]

    def test_requester_can_create_guest_invitation(self):
        before = timezone.now()

        response = self.client.post(
            self.url,
            {"guest_name": "Court Observer"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        invitation_url = response.data["invitation_url"]

        self.assertTrue(
            invitation_url.startswith(
                "http://localhost:5173/guest/join/"
            )
        )

        expires_at = parse_datetime(response.data["expires_at"])

        self.assertIsNotNone(expires_at)

        self.assertAlmostEqual(
            (expires_at - before).total_seconds(),
            3600,
            delta=2,
        )

        invitation = GuestInvitation.objects.get()

        self.assertEqual(invitation.guest_name, "Court Observer")
        self.assertEqual(invitation.created_by, self.requester)
        self.assertEqual(
            invitation.video_session.service_request,
            self.service_request,
        )

    def test_interpreter_can_create_guest_invitation(self):
        self.client.force_authenticate(self.interpreter.user)

        response = self.client.post(
            self.url,
            {"guest_name": "Witness"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        invitation = GuestInvitation.objects.get()
        self.assertEqual(invitation.created_by, self.interpreter.user)

    def test_raw_invitation_token_is_not_stored_in_database(self):
        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        raw_token = response.data["invitation_url"].rsplit("/", 1)[-1]
        expected_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

        invitation = GuestInvitation.objects.get()

        self.assertEqual(invitation.token_hash, expected_hash)
        self.assertNotEqual(invitation.token_hash, raw_token)
        self.assertEqual(len(invitation.token_hash), 64)

    def test_guest_name_is_optional(self):
        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(GuestInvitation.objects.get().guest_name, "")

    def test_unrelated_user_cannot_create_invitation(self):
        other_user = make_user(User.Role.DEAF_HOH)
        self.client.force_authenticate(other_user)

        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(GuestInvitation.objects.count(), 0)

    def test_login_is_required_to_create_invitation(self):
        self.client.force_authenticate(user=None)

        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_unassigned_request_cannot_have_guest_invitation(self):
        self.service_request.status = ServiceRequest.Status.OFFERED
        self.service_request.save(update_fields=["status"])

        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(GuestInvitation.objects.count(), 0)

    def test_ended_video_session_cannot_receive_guest_invitation(self):
        VideoSession.objects.create(
            service_request=self.service_request,
            status=VideoSession.Status.ENDED,
        )

        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(GuestInvitation.objects.count(), 0)

    @override_settings(LIVEKIT_API_KEY="", LIVEKIT_API_SECRET="")
    def test_missing_livekit_credentials_are_handled(self):
        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(
            response.status_code,
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
        self.assertEqual(GuestInvitation.objects.count(), 0)

    def test_guest_name_longer_than_100_characters_is_rejected(self):
        response = self.client.post(
            self.url,
            {"guest_name": "A" * 101},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(GuestInvitation.objects.count(), 0)

    def test_guest_can_join_with_valid_invitation_without_login(self):
        raw_token = self.create_invitation_token()

        self.client.force_authenticate(user=None)

        response = self.client.post(
            reverse("guest-invitation-join"),
            {"token": raw_token},
            format="json",
        )

        self.assertEqual(response.status_code,status.HTTP_200_OK,response.data,)
        self.assertTrue(response.data["token"])
        self.assertEqual(response.data["url"], "ws://localhost:7880")
        self.assertEqual(
            response.data["room"],
            f"session-{self.service_request.pk}",
        )

    def test_invalid_invitation_token_is_rejected(self):
        self.client.force_authenticate(user=None)

        response = self.client.post(
            reverse("guest-invitation-join"),
            {"token": "invalid-token"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_expired_invitation_is_rejected(self):
        raw_token = self.create_invitation_token()

        invitation = GuestInvitation.objects.get()
        invitation.expires_at = timezone.now() - timedelta(seconds=10)
        invitation.save(update_fields=["expires_at"])

        self.client.force_authenticate(user=None)

        response = self.client.post(
            reverse("guest-invitation-join"),
            {"token": raw_token},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_revoked_invitation_is_rejected(self):
        raw_token = self.create_invitation_token()

        invitation = GuestInvitation.objects.get()
        invitation.revoked_at = timezone.now()
        invitation.save(update_fields=["revoked_at"])

        self.client.force_authenticate(user=None)

        response = self.client.post(
            reverse("guest-invitation-join"),
            {"token": raw_token},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invitation_for_ended_session_is_rejected(self):
        raw_token = self.create_invitation_token()

        invitation = GuestInvitation.objects.get()
        invitation.video_session.status = VideoSession.Status.ENDED
        invitation.video_session.save(update_fields=["status"])

        self.client.force_authenticate(user=None)

        response = self.client.post(
            reverse("guest-invitation-join"),
            {"token": raw_token},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_guest_join_handles_missing_livekit_credentials(self):
        raw_token = self.create_invitation_token()

        self.client.force_authenticate(user=None)

        with override_settings(LIVEKIT_API_KEY="", LIVEKIT_API_SECRET=""):
            response = self.client.post(
                reverse("guest-invitation-join"),
                {"token": raw_token},
                format="json",
            )

        self.assertEqual(
            response.status_code,
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
