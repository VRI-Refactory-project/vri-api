from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.service_requests.models import ServiceRequest

User = get_user_model()
PASSWORD = "Sign-Connect-2026"

USAGE_URL = "/api/v1/dashboard/admin/reports/usage/"
RESPONSE_TIMES_URL = "/api/v1/dashboard/admin/reports/response-times/"


def make_user(role, phone, full_name="Test User"):
    return User.objects.create_user(phone, PASSWORD, full_name=full_name, role=role)


class AdminReportsAccessTests(TestCase):
    """Each report: 401 when signed out, 403 for other roles, 200 for administrators."""

    def setUp(self):
        self.client = APIClient()
        self.users = {
            role: make_user(role, f"07000007{i}0")
            for i, role in enumerate(User.Role.values)
        }

    def test_only_administrators_can_read_the_reports(self):
        for url in (USAGE_URL, RESPONSE_TIMES_URL):
            self.client.force_authenticate(user=None)
            self.assertEqual(self.client.get(url).status_code, 401, url)
            for role, user in self.users.items():
                self.client.force_authenticate(user=user)
                expected = 200 if role == User.Role.ADMIN else 403
                self.assertEqual(self.client.get(url).status_code, expected, url)


class AdminReportsTestCase(TestCase):
    def setUp(self):
        self.admin = make_user(User.Role.ADMIN, "0700000960", "Grace Admin")
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.counter = 0

    def make_request(self, **fields):
        """A service request from a new Deaf user; created_at can be moved with `created_at=`."""
        self.counter += 1
        created_at = fields.pop("created_at", None)
        user = make_user(User.Role.DEAF_HOH, f"07000006{self.counter:02d}")
        request = ServiceRequest.objects.create(requester=user, **fields)
        if created_at:
            # created_at is set automatically, so it is moved with an update.
            ServiceRequest.objects.filter(pk=request.pk).update(created_at=created_at)
        return request


class AdminUsageReportTests(AdminReportsTestCase):
    def test_counts_requests_sessions_and_statuses(self):
        status = ServiceRequest.Status
        self.make_request(status=status.COMPLETED)
        self.make_request(status=status.ASSIGNED)
        self.make_request(status=status.CANCELLED)
        self.make_request(status=status.FAILED)
        self.make_request(
            status=status.PENDING, priority=ServiceRequest.Priority.EMERGENCY
        )

        body = self.client.get(USAGE_URL).json()

        self.assertEqual(body["range_days"], 30)
        self.assertEqual(
            body["summary"],
            {
                "requests": 5,
                "sessions": 2,
                "completed": 1,
                "cancelled": 1,
                "failed": 1,
                "emergency": 1,
            },
        )
        self.assertEqual(len(body["by_day"]), 30)
        self.assertEqual(body["by_day"][-1]["requests"], 5)
        self.assertEqual(body["by_day"][-1]["sessions"], 2)
        self.assertEqual(sum(row["count"] for row in body["by_status"]), 5)

    def test_requests_outside_the_period_are_left_out(self):
        self.make_request(created_at=timezone.now() - timedelta(days=45))
        self.make_request()

        self.assertEqual(self.client.get(USAGE_URL).json()["summary"]["requests"], 1)
        wide = self.client.get(USAGE_URL, {"days": 90}).json()
        self.assertEqual(wide["summary"]["requests"], 2)
        self.assertEqual(len(wide["by_day"]), 90)

    def test_an_unknown_days_value_falls_back_to_30(self):
        for value in ("5", "abc", "-1"):
            body = self.client.get(USAGE_URL, {"days": value}).json()
            self.assertEqual(body["range_days"], 30, value)


class AdminResponseTimesReportTests(AdminReportsTestCase):
    def test_average_time_to_accept_and_lost_requests(self):
        status = ServiceRequest.Status
        now = timezone.now()
        quick = self.make_request(status=status.ASSIGNED)
        slow = self.make_request(status=status.COMPLETED)
        ServiceRequest.objects.filter(pk=quick.pk).update(
            created_at=now - timedelta(seconds=60),
            offered_at=now - timedelta(seconds=50),
            assigned_at=now - timedelta(seconds=30),
        )
        ServiceRequest.objects.filter(pk=slow.pk).update(
            created_at=now - timedelta(seconds=300),
            offered_at=now - timedelta(seconds=290),
            assigned_at=now - timedelta(seconds=180),
        )
        self.make_request(status=status.CANCELLED)
        self.make_request(status=status.FAILED)

        summary = self.client.get(RESPONSE_TIMES_URL).json()["summary"]

        self.assertEqual(summary["requests"], 4)
        self.assertEqual(summary["answered"], 2)
        self.assertEqual(summary["abandoned"], 1)
        self.assertEqual(summary["failed"], 1)
        # (30 s + 120 s) / 2 to accept, (10 s + 10 s) / 2 in the queue.
        self.assertEqual(summary["avg_seconds_to_accept"], 75)
        self.assertEqual(summary["avg_seconds_in_queue"], 10)

    def test_empty_period_has_no_averages(self):
        body = self.client.get(RESPONSE_TIMES_URL).json()

        self.assertIsNone(body["summary"]["avg_seconds_to_accept"])
        self.assertIsNone(body["offers"]["acceptance_rate_percent"])
        self.assertEqual(len(body["peak_hours"]), 24)
