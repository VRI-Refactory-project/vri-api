"""V2-009: the CREATED state, allowed status changes, the disconnect grace window, the sweep and the audit log."""

from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.audit.models import AuditLog
from apps.interpreters.models import Specialisation
from apps.service_requests.models import ServiceRequest
from apps.service_requests.services import InvalidTransition, check_transition, offer_next
from apps.service_requests.test_offers import make_interpreter, make_user

User = get_user_model()
Status = ServiceRequest.Status


def waiting_request(requester, seen_ago=None, status=Status.PENDING):
    """A request whose app last checked in `seen_ago` ago (None: never checked in)."""
    return ServiceRequest.objects.create(
        requester=requester,
        status=status,
        last_seen_at=timezone.now() - seen_ago if seen_ago is not None else None,
    )


class CreateRequestTests(APITestCase):
    def setUp(self):
        self.deaf = make_user(User.Role.DEAF_HOH)
        self.client.force_authenticate(self.deaf)
        self.url = reverse("service-request-list-create")

    def test_context_is_optional_and_defaults_to_general(self):
        # The web start-session screen sends no context.
        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["specialisation_required"], Specialisation.GENERAL)

    def test_other_is_not_a_request_context(self):
        response = self.client.post(self.url, {"specialisation_required": "OTHER"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_new_request_never_stays_created(self):
        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.data["status"], Status.PENDING)
        self.assertFalse(ServiceRequest.objects.filter(status=Status.CREATED).exists())

    def test_a_created_request_counts_as_open(self):
        waiting_request(self.deaf, status=Status.CREATED)

        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_the_refusal_says_which_request_is_open(self):
        first = self.client.post(self.url, {}, format="json")

        second = self.client.post(self.url, {}, format="json")

        self.assertEqual(second.data["open_request_id"], first.data["id"])

    def test_creating_is_audited_with_the_details(self):
        response = self.client.post(
            self.url, {"specialisation_required": "MEDICAL", "priority": "EMERGENCY"}, format="json"
        )

        entry = AuditLog.objects.get(event=AuditLog.Event.REQUEST_CREATED)
        self.assertEqual(entry.user, self.deaf)
        self.assertEqual(
            entry.details,
            {
                "request_id": response.data["id"],
                "context": "MEDICAL",
                "priority": "EMERGENCY",
                "requester_role": User.Role.DEAF_HOH,
            },
        )

    def test_a_retry_with_the_same_key_is_audited_once(self):
        self.client.post(self.url, {"request_key": "abc"}, format="json")
        self.client.post(self.url, {"request_key": "abc"}, format="json")

        self.assertEqual(AuditLog.objects.filter(event=AuditLog.Event.REQUEST_CREATED).count(), 1)

    def test_offering_is_audited(self):
        interpreter = make_interpreter()

        response = self.client.post(self.url, {}, format="json")

        entry = AuditLog.objects.get(event=AuditLog.Event.REQUEST_OFFERED)
        self.assertEqual(entry.target_user, interpreter.user)
        self.assertEqual(entry.details["request_id"], response.data["id"])
        self.assertFalse(entry.details["matched_by_fallback"])


class TransitionTests(TestCase):
    def test_allowed_moves(self):
        for current, new in [
            (Status.CREATED, Status.PENDING),
            (Status.CREATED, Status.OFFERED),
            (Status.PENDING, Status.OFFERED),
            (Status.OFFERED, Status.ASSIGNED),
            (Status.PENDING, Status.CANCELLED),
        ]:
            check_transition(current, new)

    def test_refused_moves(self):
        for current, new in [
            (Status.CANCELLED, Status.PENDING),
            (Status.ASSIGNED, Status.OFFERED),
            (Status.PENDING, Status.ASSIGNED),
            (Status.PENDING, Status.CREATED),
        ]:
            with self.subTest(current=current, new=new), self.assertRaises(InvalidTransition):
                check_transition(current, new)

    def test_a_cancelled_request_is_never_offered(self):
        cancelled = waiting_request(make_user(User.Role.DEAF_HOH), status=Status.CANCELLED)
        make_interpreter()

        with self.assertRaises(InvalidTransition):
            offer_next(cancelled)


@override_settings(REQUESTER_DISCONNECT_GRACE_WINDOW=timedelta(minutes=2))
class DisconnectWindowTests(APITestCase):
    def setUp(self):
        self.deaf = make_user(User.Role.DEAF_HOH)
        self.client.force_authenticate(self.deaf)

    def detail(self, service_request):
        return self.client.get(reverse("service-request-detail", kwargs={"pk": service_request.pk}))

    def test_checking_in_within_the_window_keeps_the_request(self):
        waiting = waiting_request(self.deaf, seen_ago=timedelta(minutes=1))

        response = self.detail(waiting)

        self.assertEqual(response.data["status"], Status.PENDING)
        waiting.refresh_from_db()
        self.assertGreater(waiting.last_seen_at, timezone.now() - timedelta(seconds=5))

    def test_a_waiting_request_is_cancelled_after_the_window(self):
        waiting = waiting_request(self.deaf, seen_ago=timedelta(minutes=3))

        response = self.detail(waiting)

        self.assertEqual(response.data["status"], Status.CANCELLED)
        self.assertEqual(response.data["cancel_reason"], ServiceRequest.CancelReason.REQUESTER_DISCONNECTED)
        self.assertIsNotNone(response.data["cancelled_at"])
        entry = AuditLog.objects.get(event=AuditLog.Event.REQUEST_CANCELLED)
        self.assertIsNone(entry.user)
        self.assertEqual(entry.target_user, self.deaf)
        self.assertEqual(entry.details["reason"], ServiceRequest.CancelReason.REQUESTER_DISCONNECTED)

    def test_a_request_never_checked_on_counts_from_when_it_was_made(self):
        waiting = waiting_request(self.deaf)
        ServiceRequest.objects.filter(pk=waiting.pk).update(created_at=timezone.now() - timedelta(minutes=3))

        self.assertEqual(self.detail(waiting).data["status"], Status.CANCELLED)

    def test_the_list_and_mine_apply_the_window_too(self):
        waiting_request(self.deaf, seen_ago=timedelta(minutes=3))

        mine = self.client.get(reverse("service-request-mine"))
        listed = self.client.get(reverse("service-request-list-create"))

        self.assertEqual(mine.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(listed.data[0]["status"], Status.CANCELLED)

    def test_an_offered_request_is_not_cancelled_for_disconnecting(self):
        offered = waiting_request(self.deaf, seen_ago=timedelta(minutes=10), status=Status.OFFERED)

        self.assertEqual(self.detail(offered).data["status"], Status.OFFERED)


@override_settings(REQUEST_SWEEP_TOKEN="test-sweep-token", REQUESTER_DISCONNECT_GRACE_WINDOW=timedelta(minutes=2))
class SweepTests(APITestCase):
    def setUp(self):
        self.url = reverse("service-request-sweep")
        self.stale = waiting_request(make_user(User.Role.DEAF_HOH), seen_ago=timedelta(minutes=5))
        self.fresh = waiting_request(make_user(User.Role.DEAF_HOH), seen_ago=timedelta(seconds=10))

    def sweep(self, token="test-sweep-token"):
        return self.client.post(self.url, HTTP_X_SWEEP_TOKEN=token)

    def test_sweep_cancels_only_stale_requests(self):
        response = self.sweep()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"cancelled_requester_disconnected": 1})
        self.stale.refresh_from_db()
        self.fresh.refresh_from_db()
        self.assertEqual(self.stale.status, Status.CANCELLED)
        self.assertEqual(self.fresh.status, Status.PENDING)

    def test_running_the_sweep_twice_changes_nothing_more(self):
        self.sweep()

        response = self.sweep()

        self.assertEqual(response.data, {"cancelled_requester_disconnected": 0})
        self.assertEqual(AuditLog.objects.filter(event=AuditLog.Event.REQUEST_CANCELLED).count(), 1)

    def test_wrong_token_is_refused(self):
        response = self.sweep(token="wrong")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.stale.refresh_from_db()
        self.assertEqual(self.stale.status, Status.PENDING)

    def test_missing_token_is_refused(self):
        self.assertEqual(self.client.post(self.url).status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(REQUEST_SWEEP_TOKEN="")
    def test_sweep_is_off_without_a_configured_token(self):
        self.assertEqual(self.sweep(token="").status_code, status.HTTP_404_NOT_FOUND)

    def test_management_command_runs_the_same_sweep(self):
        out = StringIO()

        call_command("sweep_requests", stdout=out)

        self.assertIn("cancelled_requester_disconnected: 1", out.getvalue())
        self.stale.refresh_from_db()
        self.assertEqual(self.stale.status, Status.CANCELLED)
