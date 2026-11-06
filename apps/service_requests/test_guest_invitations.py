"""V2-012: a Deaf user invites a hearing guest (no account) into an accepted session."""

import json
import urllib.error
from unittest.mock import patch

import jwt
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.service_requests.models import SessionInvitation
from apps.service_requests.services import create_request, respond_to_offer
from apps.service_requests.test_offers import make_interpreter, make_user

User = get_user_model()

TEST_SECRET = "test-secret-that-is-at-least-32-characters"
GUEST_EMAIL = "hearing.guest@example.com"

# The views import these two helpers by name, so that is where we replace them.
# Replacing them means the tests never try to reach a real LiveKit server.
IN_ROOM = "apps.service_requests.views.interpreter_is_in_room"
KICK = "apps.service_requests.views.remove_guest_from_room"


@override_settings(
    LIVEKIT_URL="ws://livekit.test:7880",
    LIVEKIT_API_KEY="testkey",
    LIVEKIT_API_SECRET=TEST_SECRET,
    WEB_APP_URL="https://app.test",
)
class GuestInvitationTests(APITestCase):
    def setUp(self):
        self.interpreter = make_interpreter()
        self.deaf = make_user(User.Role.DEAF_HOH)
        self.service_request, _ = create_request(requester=self.deaf)
        self.offer = self.service_request.offers.get()

    # --- helpers ---

    def accept(self):
        """The interpreter accepts: the session becomes ASSIGNED."""
        respond_to_offer(self.offer, "accept")
        self.service_request.refresh_from_db()

    def invite(self, user=None, email=GUEST_EMAIL):
        self.client.force_authenticate(user or self.deaf)
        url = reverse("session-invitations", kwargs={"pk": self.service_request.pk})
        return self.client.post(url, {"email": email}, format="json")

    def make_invitation(self):
        """An accepted session with one invitation already made. Returns the invitation."""
        self.accept()
        self.assertEqual(self.invite().status_code, status.HTTP_201_CREATED)
        return SessionInvitation.objects.get()

    def lobby(self, token):
        self.client.force_authenticate(None)  # guests are not signed in
        return self.client.get(reverse("guest-lobby", kwargs={"token": token}))

    def join(self, token):
        self.client.force_authenticate(None)
        return self.client.post(reverse("guest-join", kwargs={"token": token}))

    # --- inviting ---

    def test_cannot_invite_before_the_interpreter_accepts(self):
        response = self.invite()  # the request is still only OFFERED

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["code"], "session_not_ready")
        self.assertEqual(SessionInvitation.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_invite_after_acceptance_sends_an_email_with_the_link(self):
        self.accept()

        response = self.invite()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        invitation = SessionInvitation.objects.get()
        self.assertEqual(invitation.email, GUEST_EMAIL)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [GUEST_EMAIL])
        self.assertIn(f"https://app.test/join/{invitation.token}", mail.outbox[0].body)

    def test_another_deaf_user_cannot_invite_to_this_session(self):
        self.accept()

        response = self.invite(user=make_user(User.Role.DEAF_HOH))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(SessionInvitation.objects.count(), 0)

    # --- the lobby: the guest waits until the interpreter is in the call ---

    def test_lobby_says_waiting_until_the_interpreter_is_in_the_call(self):
        invitation = self.make_invitation()

        with patch(IN_ROOM, return_value=False):
            waiting = self.lobby(invitation.token)
        with patch(IN_ROOM, return_value=True):
            ready = self.lobby(invitation.token)

        self.assertEqual(waiting.status_code, status.HTTP_200_OK)
        self.assertFalse(waiting.data["interpreter_here"])
        self.assertTrue(ready.data["interpreter_here"])

    def test_guest_cannot_join_before_the_interpreter_is_in_the_call(self):
        invitation = self.make_invitation()

        with patch(IN_ROOM, return_value=False):
            response = self.join(invitation.token)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["code"], "interpreter_not_here")
        invitation.refresh_from_db()
        self.assertIsNone(invitation.joined_at)  # they have not joined yet

    # --- joining ---

    def test_guest_joins_once_the_interpreter_is_there(self):
        invitation = self.make_invitation()

        with patch(IN_ROOM, return_value=True):
            response = self.join(invitation.token)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        room = f"session-{self.service_request.pk}"
        self.assertEqual(response.data["room"], room)
        claims = jwt.decode(response.data["token"], TEST_SECRET, algorithms=["HS256"])
        self.assertEqual(claims["sub"], f"guest-{invitation.pk}")
        self.assertEqual(claims["video"]["room"], room)  # this session's room only
        invitation.refresh_from_db()
        self.assertIsNotNone(invitation.joined_at)

    def test_deaf_user_sees_when_the_guest_joined(self):
        invitation = self.make_invitation()
        with patch(IN_ROOM, return_value=True):
            self.join(invitation.token)

        self.client.force_authenticate(self.deaf)
        url = reverse("session-invitations", kwargs={"pk": self.service_request.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertIsNotNone(response.data[0]["joined_at"])
        self.assertIsNone(response.data[0]["removed_at"])

    # --- removing a guest ---

    def test_deaf_user_can_remove_the_guest_and_the_link_stops_working(self):
        invitation = self.make_invitation()
        self.client.force_authenticate(self.deaf)
        url = reverse(
            "session-invitation-remove",
            kwargs={"pk": self.service_request.pk, "invitation_id": invitation.pk},
        )

        with patch(KICK) as kick:
            response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(response.data["removed_at"])
        kick.assert_called_once()  # the guest was also disconnected from the live call
        with patch(IN_ROOM, return_value=True):
            self.assertEqual(
                self.lobby(invitation.token).status_code, status.HTTP_410_GONE
            )
            self.assertEqual(
                self.join(invitation.token).status_code, status.HTTP_410_GONE
            )

    # --- the link's life ---

    def test_link_stops_working_when_the_session_ends(self):
        invitation = self.make_invitation()
        self.service_request.ended_at = timezone.now()
        self.service_request.save(update_fields=["ended_at"])

        with patch(IN_ROOM, return_value=True):
            response = self.join(invitation.token)

        self.assertEqual(response.status_code, status.HTTP_410_GONE)

    def test_unknown_link_gets_404(self):
        self.assertEqual(
            self.lobby("not-a-real-token").status_code, status.HTTP_404_NOT_FOUND
        )

    # --- email delivery: Brevo when configured, like the recovery codes ---

    @override_settings(
        BREVO_API_KEY="test-key", DEFAULT_FROM_EMAIL="SignConnect <no-reply@app.test>"
    )
    def test_brevo_is_used_when_a_key_is_configured(self):
        self.accept()

        with patch("apps.service_requests.invitations.urllib.request.urlopen") as sent:
            response = self.invite()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        request = sent.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.brevo.com/v3/smtp/email")
        self.assertEqual(request.get_header("Api-key"), "test-key")
        body = json.loads(request.data)
        self.assertEqual(body["to"], [{"email": GUEST_EMAIL}])
        self.assertEqual(body["sender"]["email"], "no-reply@app.test")
        self.assertIn("/join/", body["textContent"])
        self.assertEqual(len(mail.outbox), 0)  # not sent twice

    @override_settings(BREVO_API_KEY="test-key")
    def test_failed_email_gives_502_and_leaves_no_invitation(self):
        self.accept()

        with patch(
            "apps.service_requests.invitations.urllib.request.urlopen",
            side_effect=urllib.error.URLError("no network"),
        ):
            response = self.invite()

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(response.data["code"], "email_failed")
        self.assertEqual(SessionInvitation.objects.count(), 0)
