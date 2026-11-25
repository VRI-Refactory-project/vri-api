"""V2-051: the booker cancels or moves an upcoming booking, within the notice rule (Clarification C-04)."""

from datetime import time, timedelta

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from apps.interpreters import scheduling
from apps.interpreters.models import InterpreterAvailability
from apps.interpreters.test_scheduling import Booking, Outcome, SchedulingBase, free_interpreter, local
from apps.service_requests.test_offers import make_user

User = get_user_model()


def also_free_on(profile, day):
    """Give an interpreter working hours on another day too."""
    InterpreterAvailability.objects.create(
        interpreter=profile, start_at=local(day, time(8, 0)), end_at=local(day, time(17, 0))
    )


@override_settings(BOOKING_CHANGE_NOTICE_HOURS=24)
class ChangeBase(SchedulingBase):
    def cancel(self, booking):
        return self.client.post(reverse("interpreters:booked-session-cancel", args=[booking.pk]))

    def reschedule(self, booking, day, at, **extra):
        body = {"date": str(day), "time": at.strftime("%H:%M"), **extra}
        return self.client.post(
            reverse("interpreters:booked-session-reschedule", args=[booking.pk]), body, format="json"
        )


class NoticeRuleTests(ChangeBase):
    def test_cancelling_inside_the_notice_window_is_refused(self):
        booking = self.book()

        with self.assertRaisesMessage(scheduling.BookingNotAvailable, "up to 24 hours before it starts"):
            scheduling.cancel_session(booking.pk, self.deaf, now=booking.starts_at - timedelta(hours=23))

        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.PENDING)

    def test_cancelling_before_the_notice_window_is_allowed(self):
        booking = self.book()

        scheduling.cancel_session(booking.pk, self.deaf, now=booking.starts_at - timedelta(hours=25))

        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CANCELLED)

    def test_moving_inside_the_notice_window_is_refused(self):
        booking = self.book()

        with self.assertRaisesMessage(scheduling.BookingNotAvailable, "can no longer be changed"):
            scheduling.reschedule_session(
                booking.pk,
                self.deaf,
                day=self.day + timedelta(days=2),
                at=time(10, 0),
                duration=45,
                now=booking.starts_at - timedelta(hours=2),
            )

    @override_settings(BOOKING_CHANGE_NOTICE_HOURS=0)
    def test_with_no_notice_changes_are_allowed_until_the_start(self):
        booking = self.book()

        scheduling.cancel_session(booking.pk, self.deaf, now=booking.starts_at - timedelta(minutes=5))

        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CANCELLED)

    def test_the_booking_list_shows_the_rule_and_the_deadline(self):
        booking = self.book()

        row = self.client.get(reverse("interpreters:booked-sessions")).data[0]

        self.assertTrue(row["can_change"])
        self.assertEqual(row["change_notice_hours"], 24)
        deadline = timezone.localtime(booking.starts_at - timedelta(hours=24))
        self.assertEqual(row["change_deadline"], deadline.isoformat())

    def test_a_booking_inside_the_window_shows_it_cannot_be_changed(self):
        soon = timezone.localtime() + timedelta(hours=3)
        booking = self.book(day=soon.date(), at=soon.time().replace(second=0, microsecond=0))

        row = next(
            r for r in self.client.get(reverse("interpreters:booked-sessions")).data if r["id"] == booking.pk
        )

        self.assertFalse(row["can_change"])


class RescheduleTests(ChangeBase):
    def setUp(self):
        super().setUp()
        self.new_day = self.day + timedelta(days=2)

    def test_a_waiting_booking_moves_and_is_offered_for_the_new_time(self):
        old_only = free_interpreter(self.day)
        booking = self.book(session_type="other")
        new_only = free_interpreter(self.new_day)

        response = self.reschedule(booking, self.new_day, time(11, 0))

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        booking.refresh_from_db()
        self.assertEqual((booking.date, booking.time), (self.new_day, time(11, 0)))
        self.assertEqual(booking.status, Booking.Status.PENDING)
        self.assertEqual(booking.offers.get(interpreter=old_only).outcome, Outcome.WITHDRAWN)
        self.assertEqual(booking.offers.get(interpreter=new_only).outcome, Outcome.PENDING)
        self.assertTrue(self.notes(self.deaf, title="Your session was moved").exists())

    def test_the_length_is_kept_unless_given(self):
        booking = self.book(duration=45)

        self.reschedule(booking, self.new_day, time(11, 0))
        booking.refresh_from_db()
        self.assertEqual(booking.duration, 45)

        self.reschedule(booking, self.new_day, time(12, 0), duration=60)
        booking.refresh_from_db()
        self.assertEqual(booking.duration, 60)

    def test_a_confirmed_interpreter_who_is_free_keeps_it(self):
        profile = free_interpreter(self.day)
        booking = self.book(session_type="other")
        scheduling.accept_offer(booking.pk, profile)
        also_free_on(profile, self.new_day)  # the same person is free on the new day too
        Booking.objects.filter(pk=booking.pk).update(reminders_sent=["day"])

        self.reschedule(booking, self.new_day, time(11, 0))

        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)
        self.assertEqual(booking.interpreter, profile)
        self.assertEqual(booking.reminders_sent, [])
        self.assertTrue(self.notes(profile.user, title="A booked session was moved").exists())
        moved = self.notes(self.deaf, title="Your session was moved").get()
        self.assertIn(f"still with {profile.user.full_name}", moved.body)

    def test_a_confirmed_interpreter_who_is_not_free_loses_it_and_it_is_offered_again(self):
        profile = free_interpreter(self.day)
        booking = self.book(session_type="other")
        scheduling.accept_offer(booking.pk, profile)
        other = free_interpreter(self.new_day)

        self.reschedule(booking, self.new_day, time(11, 0))

        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.PENDING)
        self.assertIsNone(booking.interpreter)
        self.assertEqual(booking.offers.get(interpreter=other).outcome, Outcome.PENDING)
        self.assertTrue(
            self.notes(profile.user, title="A booked session was moved to a time you are not free").exists()
        )

    def test_someone_who_declined_the_old_time_is_asked_about_the_new_one(self):
        profile = free_interpreter(self.day)
        also_free_on(profile, self.new_day)
        booking = self.book(session_type="other")
        scheduling.decline_offer(booking.pk, profile)

        self.reschedule(booking, self.new_day, time(11, 0))

        self.assertEqual(booking.offers.get(interpreter=profile).outcome, Outcome.PENDING)

    def test_reminders_follow_the_new_time(self):
        profile = free_interpreter(self.day)
        booking = self.book(session_type="other")
        scheduling.accept_offer(booking.pk, profile)
        old_start = booking.starts_at
        also_free_on(profile, self.new_day)
        self.reschedule(booking, self.new_day, time(11, 0))
        booking.refresh_from_db()

        self.assertEqual(scheduling.send_due_reminders(now=old_start - timedelta(minutes=10)), 0)
        self.assertEqual(scheduling.send_due_reminders(now=booking.starts_at - timedelta(minutes=10)), 2)

    def test_moving_a_little_later_does_not_clash_with_itself(self):
        booking = self.book(at=time(10, 0), duration=45)

        response = self.reschedule(booking, self.day, time(10, 30))

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

    def test_it_cannot_move_onto_another_of_my_bookings(self):
        booking = self.book(at=time(10, 0))
        self.book(at=time(14, 0))

        response = self.reschedule(booking, self.day, time(14, 15))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already have a session", response.data["detail"])

    def test_the_new_time_must_be_in_the_future(self):
        booking = self.book()

        response = self.reschedule(booking, timezone.localdate() - timedelta(days=1), time(10, 0))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_the_same_time_is_refused(self):
        booking = self.book(at=time(10, 0), duration=45)

        response = self.reschedule(booking, self.day, time(10, 0))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_cancelled_booking_cannot_be_moved(self):
        booking = self.book()
        self.cancel(booking)

        self.assertEqual(self.reschedule(booking, self.new_day, time(11, 0)).status_code, 400)

    def test_someone_else_cannot_move_it(self):
        booking = self.book()
        self.as_user(make_user(User.Role.DEAF_HOH))

        self.assertEqual(
            self.reschedule(booking, self.new_day, time(11, 0)).status_code, status.HTTP_404_NOT_FOUND
        )


class BookingNoticeTests(ChangeBase):
    def test_after_booking_the_booker_is_told_until_when_they_can_change_it(self):
        booking = self.book()

        note = self.notes(self.deaf, title="Session booked").get()

        deadline = timezone.localtime(booking.starts_at - timedelta(hours=24)).strftime("%a %d %b at %H:%M")
        self.assertIn(f"cancel or change it until {deadline}", note.body)
        self.assertIn("24 hours before it starts", note.body)

    def test_a_booking_that_starts_within_the_window_says_it_cannot_be_changed(self):
        soon = timezone.localtime() + timedelta(hours=3)
        self.book(day=soon.date(), at=soon.time().replace(second=0, microsecond=0))

        note = self.notes(self.deaf, title="Session booked").get()

        self.assertIn("can no longer be cancelled or changed in the app", note.body)
