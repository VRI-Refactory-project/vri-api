"""Tests for calls that never connect (RT-03), their notice (BE-02) and call audit entries (BE-03)."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.realtime.lifecycle import process_livekit_event
from apps.realtime.models import VideoSession
from apps.realtime.timeouts import _fail_if_stale, fail_stale_calls
from apps.service_requests.services import create_request, respond_to_offer
from apps.service_requests.test_offers import make_interpreter, make_user

User = get_user_model()
TIMEOUT = 60


def _ago(now, minutes):
    """A timestamp `minutes` before now, or None when nobody joined (None, not 0 minutes)."""
    return None if minutes is None else now - timedelta(minutes=minutes)


@override_settings(CALL_CONNECT_TIMEOUT_SECONDS=TIMEOUT)
class CallFailureBase(TestCase):
    def setUp(self):
        self.interpreter = make_interpreter()
        self.requester = make_user(User.Role.DEAF_HOH)
        self.service_request, _ = create_request(requester=self.requester)
        respond_to_offer(self.service_request.offers.get(), "accept")
        self.video_session = VideoSession.objects.get(service_request=self.service_request)

        self.broadcast = self.start_patch("apps.realtime.timeouts.broadcast_video_session_status")
        self.send = self.start_patch("apps.realtime.timeouts.send_to_session")
        self.lifecycle_broadcast = self.start_patch("apps.realtime.lifecycle.broadcast_video_session_status")

    def start_patch(self, target):
        patcher = patch(target)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def make_event(self, event_type, identity=None):
        return SimpleNamespace(
            event=event_type,
            room=SimpleNamespace(name=f"session-{self.service_request.pk}"),
            participant=SimpleNamespace(identity=identity) if identity else None,
        )

    def set_session(self, status, requester_minutes_ago=None, interpreter_minutes_ago=None):
        """Put the session into a known state with join times in the past."""
        now = timezone.now()
        VideoSession.objects.filter(pk=self.video_session.pk).update(
            status=status,
            requester_joined_at=_ago(now, requester_minutes_ago),
            interpreter_joined_at=_ago(now, interpreter_minutes_ago),
        )

    def refresh(self):
        self.video_session.refresh_from_db()


class StaleCallTests(CallFailureBase):
    def test_call_connecting_past_the_timeout_is_failed(self):
        self.set_session(VideoSession.Status.CONNECTING, requester_minutes_ago=5)

        self.assertEqual(fail_stale_calls(), 1)

        self.refresh()
        self.assertEqual(self.video_session.status, VideoSession.Status.FAILED)
        self.assertIsNotNone(self.video_session.ended_at)

    def test_call_connecting_within_the_timeout_is_left_alone(self):
        self.set_session(VideoSession.Status.CONNECTING, requester_minutes_ago=0)

        self.assertEqual(fail_stale_calls(), 0)

        self.refresh()
        self.assertEqual(self.video_session.status, VideoSession.Status.CONNECTING)

    def test_connected_call_is_never_failed(self):
        self.set_session(VideoSession.Status.CONNECTED, requester_minutes_ago=30, interpreter_minutes_ago=30)

        self.assertEqual(fail_stale_calls(), 0)

        self.refresh()
        self.assertEqual(self.video_session.status, VideoSession.Status.CONNECTED)

    def test_call_nobody_has_joined_yet_is_left_alone(self):
        self.set_session(VideoSession.Status.WAITING)

        self.assertEqual(fail_stale_calls(), 0)

        self.refresh()
        self.assertEqual(self.video_session.status, VideoSession.Status.WAITING)

    def test_timeout_is_measured_from_the_first_join(self):
        # The interpreter joined long ago, the requester just now: the call has been connecting since the first join.
        self.set_session(VideoSession.Status.CONNECTING, requester_minutes_ago=0, interpreter_minutes_ago=5)

        self.assertEqual(fail_stale_calls(), 1)

    def test_status_that_changed_before_the_lock_is_not_failed(self):
        # Simulates a participant joining between the candidate query and the lock: the re-check must win.
        self.set_session(VideoSession.Status.CONNECTED, requester_minutes_ago=5, interpreter_minutes_ago=5)

        self.assertFalse(_fail_if_stale(self.video_session.pk, timezone.now()))

        self.refresh()
        self.assertEqual(self.video_session.status, VideoSession.Status.CONNECTED)


class NoJoinTests(CallFailureBase):
    def created_minutes_ago(self, minutes):
        VideoSession.objects.filter(pk=self.video_session.pk).update(
            created_at=timezone.now() - timedelta(minutes=minutes)
        )

    def test_nobody_joining_in_time_fails_the_call(self):
        self.created_minutes_ago(10)

        self.assertEqual(fail_stale_calls(), 1)

        self.refresh()
        self.assertEqual(self.video_session.status, VideoSession.Status.FAILED)
        self.send.assert_called_once_with(self.service_request.pk, "call.failed", {"reason": "no_join"})

    def test_session_created_recently_is_left_alone(self):
        self.created_minutes_ago(1)

        self.assertEqual(fail_stale_calls(), 0)

        self.refresh()
        self.assertEqual(self.video_session.status, VideoSession.Status.WAITING)


class DisplayStatusTests(CallFailureBase):
    def display(self):
        from apps.realtime.call_status import display_status

        self.service_request.refresh_from_db()
        return display_status(self.service_request)

    def test_call_states_are_shown_on_the_assigned_request(self):
        cases = {
            VideoSession.Status.WAITING: "ASSIGNED",
            VideoSession.Status.CONNECTING: "CONNECTING",
            VideoSession.Status.CONNECTED: "ACTIVE",
            VideoSession.Status.ENDED: "COMPLETED",
            VideoSession.Status.FAILED: "FAILED",
        }
        for call_status, shown in cases.items():
            VideoSession.objects.filter(pk=self.video_session.pk).update(status=call_status)
            with self.subTest(call_status=call_status):
                self.assertEqual(self.display(), shown)

    def test_requests_that_are_not_assigned_keep_their_own_status(self):
        from apps.service_requests.models import ServiceRequest

        ServiceRequest.objects.filter(pk=self.service_request.pk).update(status=ServiceRequest.Status.CANCELLED)
        self.assertEqual(self.display(), "CANCELLED")


class FailureNoticeAndAuditTests(CallFailureBase):
    def test_failure_tells_everyone_on_the_session(self):
        self.set_session(VideoSession.Status.CONNECTING, requester_minutes_ago=5)

        fail_stale_calls()

        self.send.assert_called_once_with(
            self.service_request.pk, "call.failed", {"reason": "connect_timeout"}
        )
        self.broadcast.assert_called_once()

    def test_failure_writes_one_audit_entry_without_a_person(self):
        self.set_session(VideoSession.Status.CONNECTING, requester_minutes_ago=5)

        fail_stale_calls()

        entry = AuditLog.objects.get(event=AuditLog.Event.CALL_FAILED)
        self.assertIsNone(entry.user)
        self.assertEqual(entry.details, {"request_id": self.service_request.pk, "status": "failed"})

    def test_late_webhook_cannot_revive_a_failed_call(self):
        self.set_session(VideoSession.Status.CONNECTING, requester_minutes_ago=5)
        fail_stale_calls()

        process_livekit_event(self.make_event("participant_joined", f"user-{self.interpreter.user_id}"))

        self.refresh()
        self.assertEqual(self.video_session.status, VideoSession.Status.FAILED)


class CallAuditTests(CallFailureBase):
    def test_both_people_joining_writes_one_connected_entry(self):
        process_livekit_event(self.make_event("participant_joined", f"user-{self.requester.pk}"))
        process_livekit_event(self.make_event("participant_joined", f"user-{self.interpreter.user_id}"))

        self.refresh()
        self.assertEqual(self.video_session.status, VideoSession.Status.CONNECTED)
        self.assertEqual(AuditLog.objects.filter(event=AuditLog.Event.CALL_CONNECTED).count(), 1)

    def test_room_finishing_writes_an_ended_entry(self):
        process_livekit_event(self.make_event("participant_joined", f"user-{self.requester.pk}"))

        process_livekit_event(self.make_event("room_finished"))

        self.refresh()
        self.assertEqual(self.video_session.status, VideoSession.Status.ENDED)
        self.assertEqual(AuditLog.objects.filter(event=AuditLog.Event.CALL_ENDED).count(), 1)

    def test_audit_entries_carry_no_names_or_identities(self):
        process_livekit_event(self.make_event("participant_joined", f"user-{self.requester.pk}"))

        for entry in AuditLog.objects.filter(event__startswith="call_"):
            self.assertNotIn(self.requester.full_name, str(entry.details))
            self.assertNotIn(f"user-{self.requester.pk}", str(entry.details))
