"""V2-036: people hear about their session's status by push, and V2-034: the audit log says who ended it."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.audit.models import AuditLog
from apps.notifications.models import Notification
from apps.service_requests.models import ServiceRequest
from apps.service_requests.services import create_request, respond_to_offer
from apps.service_requests.status_labels import push_for
from apps.service_requests.test_offers import make_interpreter, make_user

User = get_user_model()
Status = ServiceRequest.Status


class StatusWordingTests(APITestCase):
    def test_the_push_title_is_the_on_screen_label(self):
        title, body = push_for("ASSIGNED")

        self.assertEqual(title, "Interpreter found")
        self.assertTrue(body)

    def test_states_without_wording_send_nothing(self):
        self.assertIsNone(push_for("PENDING"))
        self.assertIsNone(push_for("CANCELLED"))


class StatusPushTests(APITestCase):
    def setUp(self):
        self.interpreter = make_interpreter()
        self.deaf = make_user(User.Role.DEAF_HOH)
        self.service_request, _ = create_request(requester=self.deaf)
        self.offer = self.service_request.offers.get()

        for target in (
            "apps.service_requests.services.end_room",
            "apps.service_requests.status_broadcast.send_to_session",
        ):
            patcher = mock.patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)

    def accept(self):
        respond_to_offer(self.offer, "accept")
        self.service_request.refresh_from_db()

    def end(self, user):
        self.client.force_authenticate(user)
        url = reverse("service-request-end", kwargs={"pk": self.service_request.pk})
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(url)

    def pushes(self, user, title):
        return Notification.objects.filter(user=user, title=title)

    def test_accepting_tells_the_requester_and_not_the_interpreter(self):
        self.accept()

        sent = self.pushes(self.deaf, "Interpreter found")
        self.assertEqual(sent.count(), 1)
        self.assertEqual(sent.get().data["request_id"], self.service_request.pk)
        self.assertEqual(sent.get().data["status"], "ASSIGNED")
        self.assertFalse(
            self.pushes(self.interpreter.user, "Interpreter found").exists()
        )

    def test_ending_tells_the_interpreter_when_the_requester_ends_it(self):
        self.accept()

        self.end(self.deaf)

        self.assertEqual(
            self.pushes(self.interpreter.user, "Session completed").count(), 1
        )
        self.assertFalse(self.pushes(self.deaf, "Session completed").exists())

    def test_ending_tells_the_requester_when_the_interpreter_ends_it(self):
        self.accept()

        self.end(self.interpreter.user)

        self.assertEqual(self.pushes(self.deaf, "Session completed").count(), 1)
        self.assertFalse(
            self.pushes(self.interpreter.user, "Session completed").exists()
        )

    def test_ending_twice_sends_one_push(self):
        self.accept()

        self.end(self.deaf)
        self.end(self.deaf)

        self.assertEqual(
            Notification.objects.filter(title="Session completed").count(), 1
        )

    def test_the_audit_log_says_who_ended_the_session(self):
        self.accept()

        self.end(self.interpreter.user)

        entry = AuditLog.objects.get(event=AuditLog.Event.CALL_ENDED)
        self.assertEqual(entry.user, self.interpreter.user)
        self.assertEqual(entry.details["request_id"], self.service_request.pk)

    def test_ending_twice_writes_one_audit_entry(self):
        self.accept()

        self.end(self.deaf)
        self.end(self.deaf)

        self.assertEqual(
            AuditLog.objects.filter(event=AuditLog.Event.CALL_ENDED).count(), 1
        )
