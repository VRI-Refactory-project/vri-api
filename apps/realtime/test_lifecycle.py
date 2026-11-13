

"""Tests for LiveKit video-session lifecycle events."""

from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.realtime.lifecycle import process_livekit_event
from apps.realtime.models import VideoSession
from apps.service_requests.models import ServiceRequest, SessionInvitation
from apps.service_requests.services import create_request, respond_to_offer
from apps.service_requests.test_offers import make_interpreter, make_user

User = get_user_model()


class LiveKitLifecycleTests(TestCase):
    def setUp(self):
        self.interpreter = make_interpreter()
        self.requester = make_user(User.Role.DEAF_HOH)

        self.service_request, _ = create_request(requester=self.requester)
        offer = self.service_request.offers.get()

        respond_to_offer(offer, "accept")

        self.video_session = VideoSession.objects.get(
            service_request=self.service_request,
        )

        broadcaster = patch("apps.realtime.lifecycle.broadcast_video_session_status")
        self.broadcast = broadcaster.start()
        self.addCleanup(broadcaster.stop)

        # Completing a session on room_finished goes through end_session, which broadcasts itself.
        for target in (
            "apps.service_requests.status_broadcast.send_to_session",
            "apps.realtime.events.broadcast_video_session_status",
        ):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)

    def make_event(self, event_type, identity=None, room_name=None):
        """Build a small event object with the fields used by lifecycle.py."""
        return SimpleNamespace(
            event=event_type,
            room=SimpleNamespace(
                name=room_name or f"session-{self.service_request.pk}",
            ),
            participant=(
                SimpleNamespace(identity=identity)
                if identity
                else None
            ),
        )

    def refresh_session(self):
        self.video_session.refresh_from_db()
        self.service_request.refresh_from_db()

    def test_requester_joining_changes_status_to_connecting(self):
        event = self.make_event(
            "participant_joined",
            f"user-{self.requester.pk}",
        )

        process_livekit_event(event)

        self.refresh_session()

        self.assertEqual(
            self.video_session.status,
            VideoSession.Status.CONNECTING,
        )
        self.assertIn(
            f"user-{self.requester.pk}",
            self.video_session.joined_participants,
        )
        self.assertIsNotNone(self.video_session.requester_joined_at)

    def test_interpreter_joining_changes_status_to_connecting(self):
        event = self.make_event(
            "participant_joined",
            f"user-{self.interpreter.user_id}",
        )

        process_livekit_event(event)

        self.refresh_session()

        self.assertEqual(
            self.video_session.status,
            VideoSession.Status.CONNECTING,
        )
        self.assertIsNotNone(self.video_session.interpreter_joined_at)

    def test_both_core_participants_joining_changes_status_to_connected(self):
        process_livekit_event(
            self.make_event(
                "participant_joined",
                f"user-{self.requester.pk}",
            )
        )
        process_livekit_event(
            self.make_event(
                "participant_joined",
                f"user-{self.interpreter.user_id}",
            )
        )

        self.refresh_session()

        self.assertEqual(
            self.video_session.status,
            VideoSession.Status.CONNECTED,
        )
        self.assertIsNotNone(self.video_session.connected_at)

    def test_guest_joining_does_not_connect_the_call(self):
        invitation = SessionInvitation.objects.create(
            service_request=self.service_request,
            email="guest@example.com",
        )

        process_livekit_event(
            self.make_event(
                "participant_joined",
                f"guest-{invitation.pk}",
            )
        )

        self.refresh_session()
        invitation.refresh_from_db()

        self.assertEqual(
            self.video_session.status,
            VideoSession.Status.WAITING,
        )
        self.assertIn(
            f"guest-{invitation.pk}",
            self.video_session.joined_participants,
        )
        self.assertIsNotNone(invitation.joined_at)

    def test_participant_leaving_recalculates_status_without_ending_call(self):
        process_livekit_event(
            self.make_event(
                "participant_joined",
                f"user-{self.requester.pk}",
            )
        )
        process_livekit_event(
            self.make_event(
                "participant_joined",
                f"user-{self.interpreter.user_id}",
            )
        )
        process_livekit_event(
            self.make_event(
                "participant_left",
                f"user-{self.interpreter.user_id}",
            )
        )

        self.refresh_session()

        self.assertEqual(
            self.video_session.status,
            VideoSession.Status.CONNECTING,
        )
        self.assertNotIn(
            f"user-{self.interpreter.user_id}",
            self.video_session.joined_participants,
        )
        self.assertIsNone(self.video_session.ended_at)
        self.assertIsNone(self.service_request.ended_at)

    def test_room_finished_ends_video_session_and_service_request(self):
        process_livekit_event(
            self.make_event("room_finished")
        )

        self.refresh_session()

        self.assertEqual(
            self.video_session.status,
            VideoSession.Status.ENDED,
        )
        self.assertIsNotNone(self.video_session.ended_at)
        self.assertIsNotNone(self.service_request.ended_at)

    def test_room_finished_marks_the_session_completed_without_closing_the_room_again(self):
        with patch("apps.service_requests.services.end_room") as end_room:
            process_livekit_event(self.make_event("room_finished"))

        self.refresh_session()

        self.assertEqual(self.service_request.status, ServiceRequest.Status.COMPLETED)
        end_room.assert_not_called()

    def test_duplicate_room_finished_does_not_change_end_time(self):
        event = self.make_event("room_finished")

        process_livekit_event(event)
        self.refresh_session()
        first_ended_at = self.video_session.ended_at

        process_livekit_event(event)
        self.refresh_session()

        self.assertEqual(
            self.video_session.ended_at,
            first_ended_at,
        )

    def test_event_for_an_unknown_room_is_ignored(self):
        process_livekit_event(
            self.make_event(
                "participant_joined",
                f"user-{self.requester.pk}",
                room_name="unrelated-room",
            )
        )

        self.refresh_session()

        self.assertEqual(
            self.video_session.status,
            VideoSession.Status.WAITING,
        )
        self.assertEqual(self.video_session.joined_participants, [])

    def test_unsupported_event_is_ignored(self):
        process_livekit_event(
            self.make_event(
                "track_published",
                f"user-{self.requester.pk}",
            )
        )

        self.refresh_session()

        self.assertEqual(
            self.video_session.status,
            VideoSession.Status.WAITING,
        )
        self.assertEqual(self.video_session.joined_participants, [])
