"""Tests for people leaving a call (V2-033): deliberate leave, dropped connection, grace window and outcomes."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from livekit.protocol import models as livekit_models

from apps.audit.models import AuditLog
from apps.realtime.departures import departure_reason, expire_departures
from apps.realtime.lifecycle import process_livekit_event
from apps.realtime.models import CallDeparture, VideoSession
from apps.service_requests.models import ServiceRequest
from apps.service_requests.services import create_request, respond_to_offer
from apps.service_requests.test_offers import make_interpreter, make_user

User = get_user_model()
GRACE = 300
CLIENT_INITIATED = livekit_models.DisconnectReason.CLIENT_INITIATED
UNKNOWN = livekit_models.DisconnectReason.UNKNOWN_REASON


@override_settings(SESSION_RECONNECT_GRACE_WINDOW_SECONDS=GRACE)
class LeaveSessionBase(TestCase):
    def setUp(self):
        self.interpreter = make_interpreter()
        self.requester = make_user(User.Role.DEAF_HOH)
        self.service_request, _ = create_request(requester=self.requester)
        respond_to_offer(self.service_request.offers.get(), "accept")
        self.video_session = VideoSession.objects.get(service_request=self.service_request)
        self.requester_identity = f"user-{self.requester.pk}"
        self.interpreter_identity = f"user-{self.interpreter.user_id}"

        self.lifecycle_broadcast = self.start_patch("apps.realtime.lifecycle.broadcast_video_session_status")
        self.departure_broadcast = self.start_patch("apps.realtime.departures.broadcast_video_session_status")

    def start_patch(self, target):
        patcher = patch(target)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def event(self, event_type, identity=None, reason=UNKNOWN):
        return SimpleNamespace(
            event=event_type,
            room=SimpleNamespace(name=f"session-{self.service_request.pk}"),
            participant=(
                SimpleNamespace(identity=identity, disconnect_reason=reason) if identity else None
            ),
        )

    def join(self, identity):
        process_livekit_event(self.event("participant_joined", identity))

    def leave(self, identity, reason):
        process_livekit_event(self.event("participant_left", identity, reason))

    def both_join(self):
        self.join(self.requester_identity)
        self.join(self.interpreter_identity)

    def age_departures(self, minutes):
        """Pretend every open departure happened this many minutes ago."""
        CallDeparture.objects.filter(settled_at__isnull=True).update(
            left_at=timezone.now() - timedelta(minutes=minutes)
        )

    def refresh(self):
        self.video_session.refresh_from_db()
        self.service_request.refresh_from_db()


class DepartureReasonTests(TestCase):
    def test_a_deliberate_leave_is_recorded_as_left(self):
        participant = SimpleNamespace(identity="user-1", disconnect_reason=CLIENT_INITIATED)
        self.assertEqual(departure_reason(participant), CallDeparture.Reason.LEFT)

    def test_no_reason_from_livekit_is_treated_as_a_drop(self):
        participant = SimpleNamespace(identity="user-1", disconnect_reason=UNKNOWN)
        self.assertEqual(departure_reason(participant), CallDeparture.Reason.DROPPED)

    def test_missing_participant_details_are_treated_as_a_drop(self):
        self.assertEqual(departure_reason(None), CallDeparture.Reason.DROPPED)


class LeaveDuringCallTests(LeaveSessionBase):
    def test_deliberate_leave_does_not_end_the_call_for_the_others(self):
        self.both_join()

        self.leave(self.requester_identity, CLIENT_INITIATED)

        self.refresh()
        self.assertEqual(self.video_session.status, VideoSession.Status.CONNECTING)
        self.assertEqual(self.service_request.status, ServiceRequest.Status.ASSIGNED)
        departure = CallDeparture.objects.get()
        self.assertEqual(departure.reason, CallDeparture.Reason.LEFT)
        self.assertIsNone(departure.settled_at)

    def test_deliberate_leave_is_not_audited_as_a_disconnection(self):
        self.both_join()

        self.leave(self.requester_identity, CLIENT_INITIATED)

        self.assertFalse(AuditLog.objects.filter(event=AuditLog.Event.CALL_DISCONNECTED).exists())

    def test_dropped_connection_is_recorded_and_audited(self):
        self.both_join()

        self.leave(self.interpreter_identity, UNKNOWN)

        departure = CallDeparture.objects.get()
        self.assertEqual(departure.reason, CallDeparture.Reason.DROPPED)
        entry = AuditLog.objects.get(event=AuditLog.Event.CALL_DISCONNECTED)
        # Recorded before the status is recalculated, so the call still counts as connected at that moment.
        self.assertEqual(entry.details, {"request_id": self.service_request.pk, "status": "connected"})

    def test_a_repeated_leave_event_is_recorded_once(self):
        self.both_join()

        self.leave(self.requester_identity, UNKNOWN)
        self.leave(self.requester_identity, UNKNOWN)

        self.assertEqual(CallDeparture.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(event=AuditLog.Event.CALL_DISCONNECTED).count(), 1)

    def test_rejoining_inside_the_window_closes_the_departure(self):
        self.both_join()
        self.leave(self.requester_identity, UNKNOWN)

        self.join(self.requester_identity)

        self.assertIsNotNone(CallDeparture.objects.get().settled_at)
        self.refresh()
        self.assertEqual(self.video_session.status, VideoSession.Status.CONNECTED)

    def test_rejoining_inside_the_window_means_nothing_is_settled(self):
        self.both_join()
        self.leave(self.requester_identity, UNKNOWN)
        self.join(self.requester_identity)
        self.age_departures(minutes=10)

        self.assertEqual(expire_departures(), 0)

        self.refresh()
        self.assertEqual(self.service_request.status, ServiceRequest.Status.ASSIGNED)


class GraceWindowOutcomeTests(LeaveSessionBase):
    def test_nothing_is_settled_before_the_window_ends(self):
        self.both_join()
        self.leave(self.requester_identity, UNKNOWN)

        self.assertEqual(expire_departures(), 0)

        self.refresh()
        self.assertEqual(self.video_session.status, VideoSession.Status.CONNECTING)

    def test_a_call_that_had_connected_is_ended_and_the_request_completed(self):
        self.both_join()
        self.leave(self.interpreter_identity, UNKNOWN)
        self.leave(self.requester_identity, UNKNOWN)  # nobody left in the room
        self.age_departures(minutes=6)

        self.assertEqual(expire_departures(), 1)

        self.refresh()
        self.assertEqual(self.video_session.status, VideoSession.Status.ENDED)
        self.assertEqual(self.service_request.status, ServiceRequest.Status.COMPLETED)
        self.assertTrue(AuditLog.objects.filter(event=AuditLog.Event.CALL_ENDED).exists())

    def test_a_call_that_never_connected_is_failed_and_the_request_completed(self):
        self.join(self.requester_identity)
        self.leave(self.requester_identity, UNKNOWN)
        self.age_departures(minutes=6)

        self.assertEqual(expire_departures(), 1)

        self.refresh()
        self.assertEqual(self.video_session.status, VideoSession.Status.FAILED)
        self.assertEqual(self.service_request.status, ServiceRequest.Status.COMPLETED)
        self.assertTrue(AuditLog.objects.filter(event=AuditLog.Event.CALL_FAILED).exists())

    def test_a_deliberate_leave_also_settles_once_the_room_is_empty(self):
        self.both_join()
        self.leave(self.requester_identity, CLIENT_INITIATED)
        self.leave(self.interpreter_identity, CLIENT_INITIATED)
        self.age_departures(minutes=6)

        expire_departures()

        self.refresh()
        self.assertEqual(self.video_session.status, VideoSession.Status.ENDED)
        self.assertEqual(self.service_request.status, ServiceRequest.Status.COMPLETED)

    def test_the_call_carries_on_when_someone_is_still_in_it(self):
        self.both_join()
        self.leave(self.requester_identity, UNKNOWN)
        self.age_departures(minutes=6)

        expire_departures()

        self.refresh()
        self.assertNotIn(self.video_session.status, (VideoSession.Status.ENDED, VideoSession.Status.FAILED))
        self.assertEqual(self.service_request.status, ServiceRequest.Status.ASSIGNED)
        self.assertIsNotNone(CallDeparture.objects.get().settled_at)

    def test_settling_twice_changes_nothing_the_second_time(self):
        self.both_join()
        self.leave(self.interpreter_identity, UNKNOWN)
        self.age_departures(minutes=6)
        expire_departures()

        self.assertEqual(expire_departures(), 0)

    def test_a_late_join_cannot_revive_a_settled_call(self):
        self.join(self.requester_identity)
        self.leave(self.requester_identity, UNKNOWN)
        self.age_departures(minutes=6)
        expire_departures()

        self.join(self.requester_identity)

        self.refresh()
        self.assertEqual(self.video_session.status, VideoSession.Status.FAILED)
