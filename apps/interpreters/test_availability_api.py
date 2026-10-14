from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from .availability import monday_of
from .models import DateException, Specialisation, WeeklyAvailability
from .services import get_eligible_interpreters
from .test_availability import make_interpreter

URL = "/api/v1/interpreters/me/availability/"
MONDAY = date(2026, 10, 12)  # 12 Oct 2026 is a Monday
EAT = ZoneInfo("Africa/Kampala")


def day_url(day):
    return f"{URL}days/{day.isoformat()}/"


def next_monday():
    """A Monday in the future, so the 'no past dates' rule never gets in the way."""
    return monday_of(timezone.localdate()) + timedelta(days=7)


class WeekScheduleApiTests(APITestCase):
    def setUp(self):
        self.interpreter = make_interpreter()
        self.client.force_authenticate(self.interpreter.user)

    def test_returns_seven_days_monday_to_sunday(self):
        response = self.client.get(URL, {"week_start": "2026-10-12"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["week_start"], "2026-10-12")
        self.assertEqual(response.data["week_end"], "2026-10-18")
        self.assertEqual(len(response.data["days"]), 7)

        monday = response.data["days"][0]
        self.assertEqual(monday["date"], "2026-10-12")
        self.assertTrue(monday["is_available"])
        self.assertEqual(monday["start_time"], "08:00:00")
        self.assertEqual(monday["source"], "default")

        saturday = response.data["days"][5]
        self.assertFalse(saturday["is_available"])

    def test_any_date_in_the_week_gives_that_week(self):
        response = self.client.get(URL, {"week_start": "2026-10-14"})  # a Wednesday

        self.assertEqual(response.data["week_start"], "2026-10-12")

    def test_date_exception_shows_as_exception(self):
        DateException.objects.create(
            interpreter=self.interpreter, date=MONDAY, is_available=False
        )

        response = self.client.get(URL, {"week_start": "2026-10-12"})

        monday = response.data["days"][0]
        self.assertFalse(monday["is_available"])
        self.assertEqual(monday["source"], "exception")

    def test_exception_does_not_change_next_week(self):
        DateException.objects.create(
            interpreter=self.interpreter, date=MONDAY, is_available=False
        )

        response = self.client.get(URL, {"week_start": "2026-10-19"})

        next_monday_row = response.data["days"][0]
        self.assertTrue(next_monday_row["is_available"])
        self.assertEqual(next_monday_row["source"], "default")

    def test_modified_hours_exception_returns_its_hours(self):
        DateException.objects.create(
            interpreter=self.interpreter,
            date=MONDAY,
            is_available=True,
            start_time=time(10, 0),
            end_time=time(12, 0),
        )

        response = self.client.get(URL, {"week_start": "2026-10-12"})

        monday = response.data["days"][0]
        self.assertEqual(monday["start_time"], "10:00:00")
        self.assertEqual(monday["end_time"], "12:00:00")

    def test_bad_date_is_rejected(self):
        response = self.client.get(URL, {"week_start": "not-a-date"})

        self.assertEqual(response.status_code, 400)

    def test_login_required(self):
        self.client.force_authenticate(None)

        response = self.client.get(URL)

        self.assertIn(response.status_code, (401, 403))

    def test_only_interpreters_allowed(self):
        deaf_user = get_user_model().objects.create_user(
            phone_number="+256700000002",
            password="Pass1234!",
            full_name="Deaf User",
            role="deaf_hoh",
        )
        self.client.force_authenticate(deaf_user)

        response = self.client.get(URL)

        self.assertEqual(response.status_code, 403)


class SaveDayApiTests(APITestCase):
    def setUp(self):
        self.interpreter = make_interpreter()  # default week: Mon-Fri 08:00-17:00
        self.client.force_authenticate(self.interpreter.user)
        self.monday = next_monday()

    def save(self, day, **body):
        return self.client.patch(day_url(day), body, format="json")

    # "Repeat weekly" NOT ticked: only that date changes
    def test_this_date_only_creates_an_exception(self):
        response = self.save(self.monday, is_available=False, repeat_weekly=False)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            DateException.objects.filter(
                interpreter=self.interpreter, date=self.monday, is_available=False
            ).exists()
        )
        # the default week is untouched
        default_monday = WeeklyAvailability.objects.get(
            interpreter=self.interpreter, weekday=0
        )
        self.assertTrue(default_monday.is_available)

    def test_this_date_only_does_not_change_the_following_monday(self):
        self.save(self.monday, is_available=False, repeat_weekly=False)

        following = self.monday + timedelta(days=7)
        response = self.client.get(URL, {"week_start": following.isoformat()})

        row = response.data["days"][0]
        self.assertTrue(row["is_available"])
        self.assertEqual(row["source"], "default")

    def test_repeat_weekly_defaults_to_this_date_only(self):
        self.save(self.monday, is_available=False)  # repeat_weekly not sent

        self.assertTrue(
            DateException.objects.filter(
                interpreter=self.interpreter, date=self.monday
            ).exists()
        )

    # "Repeat weekly" ticked: the default week changes
    def test_repeat_weekly_changes_the_default_week(self):
        response = self.save(
            self.monday,
            is_available=True,
            start_time="09:00",
            end_time="15:00",
            repeat_weekly=True,
        )

        self.assertEqual(response.status_code, 200)
        default_monday = WeeklyAvailability.objects.get(
            interpreter=self.interpreter, weekday=0
        )
        self.assertEqual(default_monday.start_time, time(9, 0))
        self.assertEqual(default_monday.end_time, time(15, 0))
        self.assertFalse(DateException.objects.exists())

        following = self.monday + timedelta(days=7)
        week = self.client.get(URL, {"week_start": following.isoformat()})
        self.assertEqual(week.data["days"][0]["start_time"], "09:00:00")
        self.assertEqual(week.data["days"][0]["source"], "default")

    def test_repeat_weekly_removes_the_one_off_change_for_that_date(self):
        DateException.objects.create(
            interpreter=self.interpreter, date=self.monday, is_available=False
        )

        self.save(
            self.monday,
            is_available=True,
            start_time="09:00",
            end_time="15:00",
            repeat_weekly=True,
        )

        self.assertFalse(DateException.objects.exists())

    def test_saving_twice_updates_instead_of_duplicating(self):
        self.save(self.monday, is_available=False)
        self.save(self.monday, is_available=True, start_time="10:00", end_time="12:00")

        exceptions = DateException.objects.filter(
            interpreter=self.interpreter, date=self.monday
        )
        self.assertEqual(exceptions.count(), 1)
        self.assertEqual(exceptions.first().start_time, time(10, 0))

    # What comes back
    def test_response_is_the_updated_day(self):
        response = self.save(self.monday, is_available=False)

        self.assertEqual(response.data["date"], self.monday.isoformat())
        self.assertFalse(response.data["is_available"])
        self.assertEqual(response.data["source"], "exception")

    def test_unavailable_clears_the_times(self):
        self.save(self.monday, is_available=False, start_time="09:00", end_time="15:00")

        row = DateException.objects.get(interpreter=self.interpreter, date=self.monday)
        self.assertIsNone(row.start_time)
        self.assertIsNone(row.end_time)

    # Validation
    def test_available_needs_both_times(self):
        response = self.save(self.monday, is_available=True, start_time="09:00")

        self.assertEqual(response.status_code, 400)

    def test_end_must_be_after_start(self):
        response = self.save(
            self.monday, is_available=True, start_time="15:00", end_time="09:00"
        )

        self.assertEqual(response.status_code, 400)

    def test_past_date_is_rejected(self):
        yesterday = timezone.localdate() - timedelta(days=1)

        response = self.save(yesterday, is_available=False)

        self.assertEqual(response.status_code, 400)
        self.assertFalse(DateException.objects.exists())

    def test_bad_date_in_the_url_is_rejected(self):
        response = self.client.patch(
            f"{URL}days/not-a-date/", {"is_available": False}, format="json"
        )

        self.assertEqual(response.status_code, 400)

    # Who can call it
    def test_login_required(self):
        self.client.force_authenticate(None)

        response = self.save(self.monday, is_available=False)

        self.assertIn(response.status_code, (401, 403))

    def test_only_interpreters_allowed(self):
        deaf_user = get_user_model().objects.create_user(
            phone_number="+256700000003",
            password="Pass1234!",
            full_name="Deaf User",
            role="deaf_hoh",
        )
        self.client.force_authenticate(deaf_user)

        response = self.save(self.monday, is_available=False)

        self.assertEqual(response.status_code, 403)

    # Acceptance criterion 3, end to end: the change reaches routing right away
    def test_saved_change_applies_to_the_next_routing_decision(self):
        monday_10am = datetime.combine(self.monday, time(10, 0), tzinfo=EAT)

        def offered():
            return get_eligible_interpreters(
                specialisation_required=Specialisation.MEDICAL, now=monday_10am
            )

        self.assertIn(self.interpreter, offered())

        self.save(self.monday, is_available=False)  # an emergency on that Monday

        self.assertNotIn(self.interpreter, offered())  # very next routing call
