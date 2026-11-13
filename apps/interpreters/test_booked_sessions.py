"""The sessions Deaf users booked, as an interpreter sees them on the dashboard."""

from datetime import datetime, time, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.interpreters.models import ScheduledSession
from apps.service_requests.test_offers import make_interpreter, make_user

User = get_user_model()
Booking = ScheduledSession


class BookedSessionListTests(APITestCase):
    def setUp(self):
        self.interpreter = make_interpreter()
        self.deaf = make_user(User.Role.DEAF_HOH)
        self.url = reverse("interpreters:interpreter-booked-sessions")
        self.client.force_authenticate(self.interpreter.user)
        self.tomorrow = timezone.localdate() + timedelta(days=1)

    def book(self, *, day=None, at=time(9, 0), user=None, **extra):
        return Booking.objects.create(
            deaf_user=user or self.deaf,
            date=day or self.tomorrow,
            time=at,
            duration=extra.pop("duration", 45),
            **extra,
        )

    def test_needs_a_signed_in_user(self):
        self.client.force_authenticate(None)

        self.assertEqual(
            self.client.get(self.url).status_code, status.HTTP_401_UNAUTHORIZED
        )

    def test_deaf_users_cannot_see_everyone_elses_bookings(self):
        self.client.force_authenticate(self.deaf)

        self.assertEqual(
            self.client.get(self.url).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_shows_what_the_dashboard_needs(self):
        booking = self.book(session_type=Booking.SessionType.MEDICAL, duration=60)

        item = self.client.get(self.url).data[0]

        self.assertEqual(item["id"], booking.pk)
        self.assertEqual(item["deaf_name"], self.deaf.full_name)
        self.assertIsNone(item["deaf_photo"])
        self.assertEqual(item["date"], str(self.tomorrow))
        self.assertEqual(item["time"], "09:00:00")
        self.assertEqual(item["duration"], 60)
        self.assertEqual(item["session_type_label"], "Medical / Healthcare")
        self.assertEqual(item["status"], "PENDING")

    def test_never_includes_contact_details(self):
        self.book()

        item = self.client.get(self.url).data[0]

        self.assertNotIn("phone_number", item)
        self.assertNotIn("email", item)
        self.assertNotIn("deaf_user", item)

    def test_other_shows_the_persons_own_words(self):
        self.book(
            session_type=Booking.SessionType.OTHER, custom_session_type="Job interview"
        )

        self.assertEqual(
            self.client.get(self.url).data[0]["session_type_label"], "Job interview"
        )

    def test_lists_bookings_from_different_deaf_users_soonest_first(self):
        other = make_user(User.Role.DEAF_HOH)
        later = self.book(day=self.tomorrow + timedelta(days=2))
        sooner = self.book(user=other, at=time(8, 0))

        ids = [item["id"] for item in self.client.get(self.url).data]

        self.assertEqual(ids, [sooner.pk, later.pk])

    def test_leaves_out_cancelled_bookings(self):
        self.book(status=Booking.Status.CANCELLED)
        kept = self.book(status=Booking.Status.CONFIRMED)

        ids = [item["id"] for item in self.client.get(self.url).data]

        self.assertEqual(ids, [kept.pk])

    def test_leaves_out_days_that_have_passed(self):
        self.book(day=timezone.localdate() - timedelta(days=1))

        self.assertEqual(self.client.get(self.url).data, [])

    def test_today_keeps_only_the_times_still_to_come(self):
        now = datetime(2026, 11, 12, 10, 0, tzinfo=ZoneInfo("Africa/Kampala"))
        today = now.date()
        self.book(day=today, at=time(9, 0))  # already started
        coming = self.book(day=today, at=time(15, 30))

        with mock.patch("apps.interpreters.views.timezone.localtime", return_value=now):
            ids = [item["id"] for item in self.client.get(self.url).data]

        self.assertEqual(ids, [coming.pk])
