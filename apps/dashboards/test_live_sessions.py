"""V2-047: the administrator's live-session monitor, its needs-attention rules, flags and live stream."""

import json
from datetime import timedelta

from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.audit.models import AuditLog
from apps.dashboards import live_sessions
from apps.realtime.consumers import CLOSE_BAD_TICKET, CLOSE_NOT_ALLOWED
from apps.realtime.events import broadcast_video_session_status
from apps.realtime.models import SessionAcknowledgement, SessionFlag, VideoSession
from apps.realtime.tickets import issue_ticket
from apps.service_requests.models import ServiceRequest
from apps.service_requests.services import create_request, respond_to_offer
from apps.service_requests.test_offers import make_interpreter, make_user
from config.asgi import application

User = get_user_model()
Live = VideoSession.Status
LIST_URL = reverse("admin-live-sessions")
STATS_URL = reverse("admin-session-stats")


def start_call(requester=None, **request_fields):
    """A request an interpreter accepted, so its video session exists (waiting)."""
    make_interpreter()
    service_request, _ = create_request(requester=requester or make_user(User.Role.DEAF_HOH), **request_fields)
    respond_to_offer(service_request.offers.get(), "accept")
    return VideoSession.objects.get(service_request=service_request)


def set_call(call, **fields):
    VideoSession.objects.filter(pk=call.pk).update(**fields)
    call.refresh_from_db()


def ago(**kwargs):
    return timezone.now() - timedelta(**kwargs)


@override_settings(
    CALL_WAIT_TIMEOUT_SECONDS=300,
    CALL_CONNECT_TIMEOUT_SECONDS=60,
    UNUSUAL_DURATION_THRESHOLD_MINUTES=90,
)
class LiveMonitorBase(APITestCase):
    def setUp(self):
        self.admin = make_user(User.Role.ADMIN)
        self.client.force_authenticate(self.admin)

    def rows(self, **params):
        response = self.client.get(LIST_URL, params)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return response.data

    def row_for(self, call, **params):
        return next(row for row in self.rows(**params) if row["id"] == call.service_request_id)


class LiveListTests(LiveMonitorBase):
    def test_a_row_is_metadata_only(self):
        call = start_call(description="The patient says their chest hurts")

        row = self.row_for(call)

        # BE-03: exactly these fields, and nothing from what was said.
        self.assertEqual(set(row), live_sessions.LIVE_ROW_FIELDS)
        self.assertNotIn("chest hurts", json.dumps(row))

    def test_a_row_shows_who_is_on_the_call(self):
        call = start_call()
        request = call.service_request

        row = self.row_for(call)

        self.assertEqual(row["requester"], {
            "name": request.requester.full_name,
            "role": User.Role.DEAF_HOH,
            "role_label": "Deaf user",
        })
        self.assertEqual(row["interpreter"], {"name": request.interpreter.user.full_name})
        self.assertEqual(row["sector"], "Individual")
        self.assertEqual(row["channel"], "on_demand")
        self.assertEqual(row["status"], Live.WAITING)
        self.assertEqual(row["session_type"], "GENERAL")

    def test_minutes_running_count_from_when_the_call_connected(self):
        call = start_call()
        set_call(call, status=Live.CONNECTED, connected_at=ago(minutes=12))

        self.assertEqual(self.row_for(call)["minutes_running"], 12)

    def test_finished_calls_are_not_listed(self):
        ended = start_call()
        set_call(ended, status=Live.ENDED, ended_at=timezone.now())
        failed = start_call()
        set_call(failed, status=Live.FAILED)

        self.assertEqual(self.rows(), [])

    def test_the_connecting_filter_leaves_out_connected_calls(self):
        waiting = start_call()
        connected = start_call()
        set_call(connected, status=Live.CONNECTED, connected_at=timezone.now())

        ids = [row["id"] for row in self.rows(filter="connecting")]

        self.assertEqual(ids, [waiting.service_request_id])

    def test_the_attention_filter_keeps_only_calls_with_a_reason(self):
        start_call()
        emergency = start_call(priority=ServiceRequest.Priority.EMERGENCY)

        ids = [row["id"] for row in self.rows(filter="attention")]

        self.assertEqual(ids, [emergency.service_request_id])

    def test_an_unknown_filter_is_refused(self):
        response = self.client.get(LIST_URL, {"filter": "everything"})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "invalid_filter")

    def test_search_by_name_or_session_number(self):
        first = start_call()
        second = start_call()
        name = first.service_request.requester.full_name

        self.assertEqual([row["id"] for row in self.rows(search=name)], [first.service_request_id])
        self.assertEqual(
            [row["id"] for row in self.rows(search=f"#{second.service_request_id}")],
            [second.service_request_id],
        )

    def test_only_system_administrators_can_see_the_monitor(self):
        self.client.force_authenticate(make_user(User.Role.DEAF_HOH))
        self.assertEqual(self.client.get(LIST_URL).status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.client.get(STATS_URL).status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(LIST_URL).status_code, status.HTTP_401_UNAUTHORIZED)


class NeedsAttentionTests(LiveMonitorBase):
    def reasons(self, call):
        return self.row_for(call)["needs_attention"]

    def test_a_normal_new_call_needs_nothing(self):
        self.assertEqual(self.reasons(start_call()), [])

    def test_an_emergency_needs_attention(self):
        call = start_call(priority=ServiceRequest.Priority.EMERGENCY)

        self.assertEqual(self.reasons(call), [live_sessions.EMERGENCY])

    def test_waiting_past_half_the_wait_time_out_is_stuck(self):
        call = start_call()
        set_call(call, created_at=ago(seconds=151))  # time-out 300 s

        self.assertEqual(self.reasons(call), [live_sessions.STUCK])

    def test_waiting_a_short_while_is_not_stuck(self):
        call = start_call()
        set_call(call, created_at=ago(seconds=60))

        self.assertEqual(self.reasons(call), [])

    def test_connecting_past_half_the_connect_time_out_is_stuck(self):
        call = start_call()
        set_call(call, status=Live.CONNECTING, requester_joined_at=ago(seconds=31))  # time-out 60 s

        self.assertEqual(self.reasons(call), [live_sessions.STUCK])

    def test_a_call_over_the_threshold_is_flagged_as_over_duration(self):
        call = start_call()
        set_call(call, status=Live.CONNECTED, connected_at=ago(minutes=91))

        self.assertEqual(self.reasons(call), [live_sessions.OVER_DURATION])

    def test_a_call_under_the_threshold_is_fine(self):
        call = start_call()
        set_call(call, status=Live.CONNECTED, connected_at=ago(minutes=89))

        self.assertEqual(self.reasons(call), [])

    @override_settings(UNUSUAL_DURATION_THRESHOLD_MINUTES=30)
    def test_the_threshold_comes_from_the_setting(self):
        call = start_call()
        set_call(call, status=Live.CONNECTED, connected_at=ago(minutes=31))

        self.assertEqual(self.reasons(call), [live_sessions.OVER_DURATION])

    def test_a_flagged_call_needs_attention(self):
        call = start_call()
        SessionFlag.objects.create(video_session=call, flagged_by=self.admin)

        self.assertEqual(self.reasons(call), [live_sessions.FLAGGED])


class LiveStatsTests(LiveMonitorBase):
    def test_the_figures_count_live_calls_only(self):
        start_call()  # waiting
        connected = start_call(priority=ServiceRequest.Priority.EMERGENCY)
        set_call(
            connected,
            status=Live.CONNECTED,
            requester_joined_at=ago(seconds=40),
            interpreter_joined_at=ago(seconds=30),
            connected_at=ago(seconds=20),
        )
        failed = start_call()
        set_call(failed, status=Live.FAILED)

        stats = self.client.get(STATS_URL).data

        self.assertEqual(stats["liveNow"], 1)
        self.assertEqual(stats["connecting"], 1)
        self.assertEqual(stats["needsAttention"], 1)  # the emergency; the failed call does not count
        self.assertEqual(stats["attention"]["emergency"], 1)
        self.assertEqual(stats["connectingAverageSeconds"], 20)  # first join to connected
        self.assertNotIn("liveStable", stats)

    def test_with_nothing_connected_today_the_average_is_zero(self):
        self.assertEqual(self.client.get(STATS_URL).data["connectingAverageSeconds"], 0)


class FlagTests(LiveMonitorBase):
    def setUp(self):
        super().setUp()
        self.call = start_call()
        self.url = reverse("admin-session-flag", args=[self.call.service_request_id])

    def test_flagging_records_who_and_why_and_audits_it(self):
        response = self.client.post(self.url, {"note": "Check the interpreter's video"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["flag"]["flagged_by"], self.admin.full_name)
        self.assertEqual(response.data["flag"]["note"], "Check the interpreter's video")
        self.assertIn(live_sessions.FLAGGED, response.data["needs_attention"])
        entry = AuditLog.objects.get(event=AuditLog.Event.SESSION_FLAGGED)
        self.assertEqual(entry.user, self.admin)
        self.assertEqual(entry.details["request_id"], self.call.service_request_id)

    def test_the_note_is_optional(self):
        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["flag"]["note"], "")

    def test_a_session_cannot_be_flagged_twice(self):
        self.client.post(self.url, {}, format="json")

        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(SessionFlag.objects.count(), 1)

    def test_a_finished_session_cannot_be_flagged(self):
        set_call(self.call, status=Live.ENDED)

        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "session_not_live")

    def test_clearing_keeps_the_history_and_audits_it(self):
        self.client.post(self.url, {}, format="json")

        response = self.client.delete(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["flag"])
        self.assertEqual(response.data["needs_attention"], [])
        flag = SessionFlag.objects.get()
        self.assertEqual(flag.cleared_by, self.admin)
        self.assertIsNotNone(flag.cleared_at)
        self.assertTrue(AuditLog.objects.filter(event=AuditLog.Event.SESSION_FLAG_CLEARED).exists())

    def test_it_can_be_flagged_again_after_clearing(self):
        self.client.post(self.url, {}, format="json")
        self.client.delete(self.url)

        self.assertEqual(self.client.post(self.url, {}, format="json").status_code, status.HTTP_201_CREATED)

    def test_marking_done_also_marks_the_current_warnings_as_seen(self):
        set_call(self.call, status=Live.CONNECTED, connected_at=ago(minutes=95))
        self.client.post(self.url, {}, format="json")

        response = self.client.delete(self.url)

        self.assertEqual(response.data["needs_attention"], [])
        self.assertEqual(response.data["seen"]["reasons"], [live_sessions.OVER_DURATION])
        self.assertEqual(response.data["seen"]["seen_by"], self.admin.full_name)
        entry = AuditLog.objects.get(event=AuditLog.Event.SESSION_FLAG_CLEARED)
        self.assertEqual(entry.details["marked_seen"], [live_sessions.OVER_DURATION])

    def test_clearing_with_no_flag_is_not_found(self):
        self.assertEqual(self.client.delete(self.url).status_code, status.HTTP_404_NOT_FOUND)

    def test_only_system_administrators_can_flag(self):
        self.client.force_authenticate(make_user(User.Role.INTERPRETER))

        self.assertEqual(self.client.post(self.url, {}, format="json").status_code, status.HTTP_403_FORBIDDEN)

    def test_an_unknown_session_is_not_found(self):
        url = reverse("admin-session-flag", args=[999999])

        self.assertEqual(self.client.post(url, {}, format="json").status_code, status.HTTP_404_NOT_FOUND)


# The consumer reads the database from another thread, which only sees committed data, hence
# TransactionTestCase. In memory, so the tests need no Redis.
@override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}})
class AdminSocketTests(TransactionTestCase):
    async def open(self, ticket):
        socket = WebsocketCommunicator(application, f"/ws/admin/sessions/?ticket={ticket}")
        connected, _ = await socket.connect()
        self.assertTrue(connected)
        return socket

    async def test_an_admin_gets_in(self):
        admin = await sync_to_async(make_user)(User.Role.ADMIN)
        socket = await self.open(issue_ticket(admin))

        self.assertEqual(await socket.receive_json_from(), {"type": "live_sessions.ready"})
        await socket.disconnect()

    async def test_anyone_else_is_turned_away(self):
        deaf = await sync_to_async(make_user)(User.Role.DEAF_HOH)
        socket = await self.open(issue_ticket(deaf))

        self.assertEqual(await socket.receive_output(), {"type": "websocket.close", "code": CLOSE_NOT_ALLOWED})

    async def test_a_bad_ticket_is_turned_away(self):
        socket = await self.open("not-a-ticket")

        self.assertEqual(await socket.receive_output(), {"type": "websocket.close", "code": CLOSE_BAD_TICKET})

    async def test_a_call_status_change_reaches_the_admin(self):
        admin = await sync_to_async(make_user)(User.Role.ADMIN)
        socket = await self.open(issue_ticket(admin))
        await socket.receive_json_from()  # ready
        call = await sync_to_async(start_call)()
        await socket.receive_json_from()  # the call opening (waiting)

        def connect_call():
            with transaction.atomic():
                set_call(call, status=Live.CONNECTED, connected_at=timezone.now())
                broadcast_video_session_status(call)

        await sync_to_async(connect_call)()

        message = await socket.receive_json_from()
        self.assertEqual(message["type"], "live_session.updated")
        self.assertEqual(message["session"]["id"], call.service_request_id)
        self.assertEqual(message["session"]["status"], Live.CONNECTED)
        self.assertEqual(set(message["session"]), live_sessions.LIVE_ROW_FIELDS)
        await socket.disconnect()


class SeenTests(LiveMonitorBase):
    """Option B: an admin marks a call's warnings as seen, so it leaves "Needs attention"."""

    def setUp(self):
        super().setUp()
        self.call = start_call()
        set_call(self.call, status=Live.CONNECTED, connected_at=ago(minutes=95))
        self.url = reverse("admin-session-seen", args=[self.call.service_request_id])

    def test_seen_warnings_stop_counting_but_stay_listed(self):
        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["needs_attention"], [])
        self.assertEqual(response.data["seen"]["reasons"], [live_sessions.OVER_DURATION])
        self.assertEqual(self.rows(filter="attention"), [])
        self.assertEqual(self.client.get(STATS_URL).data["needsAttention"], 0)
        entry = AuditLog.objects.get(event=AuditLog.Event.SESSION_ACKNOWLEDGED)
        self.assertEqual(entry.user, self.admin)
        self.assertEqual(entry.details["reasons"], [live_sessions.OVER_DURATION])

    def test_a_new_warning_after_seen_still_shows(self):
        self.client.post(self.url)
        ServiceRequest.objects.filter(pk=self.call.service_request_id).update(
            priority=ServiceRequest.Priority.EMERGENCY
        )

        row = self.row_for(self.call)

        self.assertEqual(row["needs_attention"], [live_sessions.EMERGENCY])
        self.assertEqual(row["seen"]["reasons"], [live_sessions.OVER_DURATION])

    def test_a_follow_up_mark_still_counts_after_seen(self):
        self.client.post(self.url)
        SessionFlag.objects.create(video_session=self.call, flagged_by=self.admin)

        self.assertEqual(self.row_for(self.call)["needs_attention"], [live_sessions.FLAGGED])

    def test_seen_drops_off_once_the_warning_is_no_longer_true(self):
        self.client.post(self.url)
        set_call(self.call, connected_at=ago(minutes=10))

        row = self.row_for(self.call)

        self.assertIsNone(row["seen"])
        self.assertEqual(row["needs_attention"], [])

    def test_a_call_with_no_warnings_has_nothing_to_mark(self):
        set_call(self.call, connected_at=ago(minutes=10))

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "nothing_to_mark")
        self.assertFalse(SessionAcknowledgement.objects.exists())

    def test_a_finished_call_cannot_be_marked(self):
        set_call(self.call, status=Live.ENDED)

        self.assertEqual(self.client.post(self.url).data["code"], "session_not_live")

    def test_only_system_administrators_can_mark_seen(self):
        self.client.force_authenticate(make_user(User.Role.DEAF_HOH))

        self.assertEqual(self.client.post(self.url).status_code, status.HTTP_403_FORBIDDEN)
