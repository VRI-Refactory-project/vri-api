from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.audit.models import AuditLog
from apps.interpreters.models import InterpreterProfile, VerificationStatus
from apps.service_requests.models import ServiceRequest

User = get_user_model()
PASSWORD = "Sign-Connect-2026"

STATS_URLS = [
    "/api/v1/dashboard/admin/user-growth/",
    "/api/v1/dashboard/admin/sessions-activity/",
    "/api/v1/dashboard/admin/session-types/",
    "/api/v1/dashboard/admin/verification-status/",
    "/api/v1/dashboard/admin/activity/",
]


def make_user(role, phone, full_name="Test User", **extra):
    return User.objects.create_user(
        phone, PASSWORD, full_name=full_name, role=role, **extra
    )


class AdminStatsAccessTests(TestCase):
    """Each chart endpoint: 401 when signed out, 403 for other roles, 200 for admins."""

    def setUp(self):
        self.client = APIClient()
        self.users = {
            role: make_user(role, f"07000009{i}0")
            for i, role in enumerate(User.Role.values)
        }

    def test_only_administrators_can_read_the_charts(self):
        for url in STATS_URLS:
            self.client.force_authenticate(user=None)
            self.assertEqual(self.client.get(url).status_code, 401, url)
            for role, user in self.users.items():
                self.client.force_authenticate(user=user)
                expected = 200 if role == User.Role.ADMIN else 403
                self.assertEqual(self.client.get(url).status_code, expected, url)


class AdminStatsTestCase(TestCase):
    def setUp(self):
        self.admin = make_user(User.Role.ADMIN, "0700000950", full_name="Grace Admin")
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.phone_counter = 0

    def new_user(self, role=User.Role.DEAF_HOH, **extra):
        self.phone_counter += 1
        return make_user(role, f"07000008{self.phone_counter:02d}", **extra)

    def new_request(self, status, assigned_at=None):
        return ServiceRequest.objects.create(
            requester=self.new_user(), status=status, assigned_at=assigned_at
        )


class UserGrowthTests(AdminStatsTestCase):
    url = "/api/v1/dashboard/admin/user-growth/"

    def test_gives_six_months_ending_with_this_month(self):
        results = self.client.get(self.url).json()["results"]
        self.assertEqual(len(results), 6)
        self.assertEqual(results[-1]["label"], timezone.localtime().strftime("%b"))

    def test_counts_users_who_joined_before_the_window_in_every_point(self):
        long_ago = timezone.now() - timedelta(days=240)
        self.new_user(date_joined=long_ago)
        results = self.client.get(self.url).json()["results"]
        self.assertEqual(results[0]["value"], 1)

    def test_ends_with_everyone_who_is_active_now(self):
        self.new_user(date_joined=timezone.now() - timedelta(days=240))
        self.new_user(date_joined=timezone.now() - timedelta(days=40))
        self.new_user()
        results = self.client.get(self.url).json()["results"]
        self.assertEqual(results[-1]["value"], User.objects.filter(is_active=True).count())
        values = [point["value"] for point in results]
        self.assertEqual(values, sorted(values))

    def test_deactivated_users_are_not_counted(self):
        self.new_user(is_active=False)
        results = self.client.get(self.url).json()["results"]
        self.assertEqual(results[-1]["value"], 1)  # only the administrator


class SessionsActivityTests(AdminStatsTestCase):
    url = "/api/v1/dashboard/admin/sessions-activity/"

    def test_gives_seven_days_of_zeros_when_nothing_happened(self):
        data = self.client.get(self.url).json()
        self.assertEqual(len(data["labels"]), 7)
        self.assertEqual(data["values"], [0] * 7)

    def test_counts_sessions_by_the_day_they_started(self):
        now = timezone.now()
        self.new_request(ServiceRequest.Status.ASSIGNED, assigned_at=now)
        self.new_request(ServiceRequest.Status.COMPLETED, assigned_at=now)
        self.new_request(
            ServiceRequest.Status.COMPLETED, assigned_at=now - timedelta(days=10)
        )
        self.new_request(ServiceRequest.Status.PENDING)
        data = self.client.get(self.url).json()
        self.assertEqual(data["values"][-1], 2)
        self.assertEqual(sum(data["values"]), 2)


class SessionTypesTests(AdminStatsTestCase):
    url = "/api/v1/dashboard/admin/session-types/"

    def test_all_zero_when_there_are_no_requests(self):
        self.assertEqual(
            self.client.get(self.url).json(),
            {"vri": 0, "onsite": 0, "pending": 0, "total": 0},
        )

    def test_counts_held_sessions_and_waiting_requests(self):
        Status = ServiceRequest.Status
        for status in (
            Status.ASSIGNED,
            Status.COMPLETED,
            Status.PENDING,
            Status.OFFERED,
            Status.CANCELLED,
            Status.FAILED,
        ):
            self.new_request(status)
        self.assertEqual(
            self.client.get(self.url).json(),
            {"vri": 2, "onsite": 0, "pending": 2, "total": 4},
        )


class VerificationStatusTests(AdminStatsTestCase):
    url = "/api/v1/dashboard/admin/verification-status/"

    def test_all_zero_when_there_are_no_interpreters(self):
        self.assertEqual(
            self.client.get(self.url).json(),
            {"approved": 0, "pending": 0, "rejected": 0, "flagged": 0, "total": 0},
        )

    def test_counts_interpreters_by_status(self):
        statuses = [
            VerificationStatus.APPROVED,
            VerificationStatus.APPROVED,
            VerificationStatus.INACTIVE,
            VerificationStatus.REJECTED,
            VerificationStatus.DEACTIVATED,
        ]
        for status in statuses:
            InterpreterProfile.objects.create(
                user=self.new_user(User.Role.INTERPRETER), verification_status=status
            )
        self.assertEqual(
            self.client.get(self.url).json(),
            {"approved": 2, "pending": 1, "rejected": 1, "flagged": 1, "total": 5},
        )


class ActivityFeedTests(AdminStatsTestCase):
    url = "/api/v1/dashboard/admin/activity/"

    def test_empty_when_nothing_has_been_recorded(self):
        self.assertEqual(self.client.get(self.url).json(), [])

    def test_describes_who_did_what_and_when(self):
        AuditLog.objects.create(
            user=self.admin, event=AuditLog.Event.INSTITUTION_APPROVED
        )
        item = self.client.get(self.url).json()[0]
        self.assertEqual(item["text"], "Grace Admin: Institution approved")
        self.assertEqual(item["time"], "Just now")
        self.assertIn("created_at", item)

    def test_entries_without_a_user_show_only_what_happened(self):
        AuditLog.objects.create(user=None, event=AuditLog.Event.REQUEST_FAILED)
        item = self.client.get(self.url).json()[0]
        self.assertEqual(
            item["text"], "Interpreter request failed (no interpreter in time)"
        )

    def test_shows_only_the_latest_ten_newest_first(self):
        for _ in range(12):
            AuditLog.objects.create(
                user=self.admin, event=AuditLog.Event.ADMIN_SIGN_IN
            )
        AuditLog.objects.create(user=self.admin, event=AuditLog.Event.STAFF_REVOKED)
        items = self.client.get(self.url).json()
        self.assertEqual(len(items), 10)
        self.assertEqual(
            items[0]["text"], "Grace Admin: Institution staff access revoked"
        )
