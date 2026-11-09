from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from apps.service_requests.models import ServiceRequest

from .status_broadcast import broadcast_status
from .status_labels import STATUS_LABELS, label_for


class LabelTests(SimpleTestCase):
    def test_every_request_status_has_a_label_that_is_not_the_raw_name(self):
        for status in ServiceRequest.Status.values:
            self.assertIn(status, STATUS_LABELS)
            self.assertNotEqual(label_for(status), status)

    def test_ending_outcomes_have_distinct_labels(self):
        ends = {label_for(s) for s in ("COMPLETED", "CANCELLED", "FAILED")}
        self.assertEqual(len(ends), 3)

    def test_unknown_state_never_leaks_its_name(self):
        self.assertNotIn("WEIRD", label_for("WEIRD"))


class BroadcastTests(SimpleTestCase):
    def test_sends_status_and_label_to_the_session(self):
        request = SimpleNamespace(pk=7, status="ASSIGNED")
        with mock.patch(
            "apps.service_requests.status_broadcast.send_to_session"
        ) as send:
            broadcast_status(request)
        send.assert_called_once_with(
            7, "session.status", {"status": "ASSIGNED", "label": "Interpreter found"}
        )
