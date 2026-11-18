"""Booked sessions: offering them to interpreters, confirming them, cancelling, and admin follow-up."""

from datetime import datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.admin.sites import site
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.interpreters import scheduling
from apps.interpreters.admin import ScheduledSessionAdmin
from apps.interpreters.models import (
    DateException,
    InterpreterAvailability,
    InterpreterProfile,
    ScheduledSession,
    ScheduledSessionOffer,
    Specialisation,
    VerificationStatus,
)
from apps.notifications.models import Notification
from apps.service_requests.test_offers import make_user

User = get_user_model()
Booking = ScheduledSession
Outcome = ScheduledSessionOffer.Outcome


def local(day, at):
    return timezone.make_aware(datetime.combine(day, at))


def free_interpreter(
    day, *, specialisations=None, opens=time(8, 0), closes=time(17, 0), **extra
):
    """An approved interpreter with saved availability on `day`. Their dashboard switch is OFF,
    which must not matter for a booking next week."""
    profile = InterpreterProfile.objects.create(
        user=make_user(User.Role.INTERPRETER),
        verification_status=VerificationStatus.APPROVED,
        is_available=False,
        specialisation=Specialisation.GENERAL,
        specialisations=specialisations or [],
        **extra,
    )
    InterpreterAvailability.objects.create(
        interpreter=profile, start_at=local(day, opens), end_at=local(day, closes)
    )
    return profile


class SchedulingBase(APITestCase):
    def setUp(self):
        self.deaf = make_user(User.Role.DEAF_HOH)
        self.day = timezone.localdate() + timedelta(days=3)
        self.client.force_authenticate(self.deaf)

    def book(
        self, at=time(10, 0), duration=45, session_type="medical", day=None, **extra
    ):
        body = {
            "date": str(day or self.day),
            "time": at.strftime("%H:%M"),
            "duration": duration,
            "session_type": session_type,
            **extra,
        }
        if session_type == "other":
            body.setdefault("custom_session_type", "A family matter")
        response = self.client.post(
            reverse("interpreters:schedule-session"), body, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        return Booking.objects.get(pk=response.data["id"])

    def as_user(self, user):
        self.client.force_authenticate(user)

    def answer(self, profile, name, booking):
        self.as_user(profile.user)
        return self.client.post(
            reverse(f"interpreters:booked-session-{name}", args=[booking.pk])
        )

    def notes(self, user, **filters):
        return Notification.objects.filter(user=user, **filters)


class OfferingTests(SchedulingBase):
    def test_a_new_booking_is_offered_to_every_free_interpreter(self):
        first, second = free_interpreter(self.day), free_interpreter(self.day)

        booking = self.book(session_type="other")

        self.assertEqual(booking.status, Booking.Status.PENDING)
        self.assertIsNone(booking.interpreter)
        self.assertIsNotNone(booking.offered_at)
        self.assertEqual(
            set(booking.offers.values_list("interpreter_id", flat=True)),
            {first.pk, second.pk},
        )
        for profile in (first, second):
            note = self.notes(
                profile.user, category=Notification.Category.INCOMING_REQUEST
            ).get()
            self.assertEqual(note.data["session_id"], booking.pk)

    def test_the_dashboard_switch_does_not_matter_for_a_future_booking(self):
        profile = free_interpreter(self.day)
        self.assertFalse(profile.is_available)

        booking = self.book(session_type="other")

        self.assertTrue(booking.offers.filter(interpreter=profile).exists())

    def test_nobody_free_leaves_it_pending_with_no_offers(self):
        booking = self.book()

        self.assertEqual(booking.status, Booking.Status.PENDING)
        self.assertFalse(booking.offers.exists())
        self.assertIsNone(booking.offered_at)

    def test_only_people_who_work_the_whole_slot_are_offered_it(self):
        works = free_interpreter(self.day)
        free_interpreter(self.day, opens=time(11, 0))  # starts after the booking does
        free_interpreter(self.day, closes=time(10, 30))  # leaves before it ends

        booking = self.book(session_type="other", at=time(10, 0), duration=45)

        self.assertEqual(
            list(booking.offers.values_list("interpreter_id", flat=True)), [works.pk]
        )

    def test_the_weekly_schedule_is_used_when_no_periods_are_saved(self):
        profile = InterpreterProfile.objects.create(
            user=make_user(User.Role.INTERPRETER),
            verification_status=VerificationStatus.APPROVED,
        )
        DateException.objects.create(
            interpreter=profile,
            date=self.day,
            is_available=True,
            start_time=time(9, 0),
            end_time=time(12, 0),
        )
        off = InterpreterProfile.objects.create(
            user=make_user(User.Role.INTERPRETER),
            verification_status=VerificationStatus.APPROVED,
        )
        DateException.objects.create(interpreter=off, date=self.day, is_available=False)

        booking = self.book(session_type="other")

        self.assertEqual(
            list(booking.offers.values_list("interpreter_id", flat=True)), [profile.pk]
        )

    def test_unapproved_and_deactivated_interpreters_are_not_offered_it(self):
        pending = free_interpreter(self.day)
        pending.verification_status = VerificationStatus.INACTIVE
        pending.save()
        gone = free_interpreter(self.day)
        gone.user.is_active = False
        gone.user.save()

        booking = self.book(session_type="other")

        self.assertFalse(booking.offers.exists())

    def test_someone_with_another_confirmed_session_in_that_slot_is_not_offered_it(
        self,
    ):
        busy, free = free_interpreter(self.day), free_interpreter(self.day)
        first = self.book(session_type="other", at=time(10, 0))
        scheduling.accept_offer(first.pk, busy)

        second = self.book(session_type="other", at=time(10, 30))

        self.assertEqual(
            list(second.offers.values_list("interpreter_id", flat=True)), [free.pk]
        )

    def test_back_to_back_sessions_do_not_clash(self):
        profile = free_interpreter(self.day)
        first = self.book(session_type="other", at=time(10, 0), duration=60)
        scheduling.accept_offer(first.pk, profile)

        second = self.book(session_type="other", at=time(11, 0))

        self.assertTrue(second.offers.filter(interpreter=profile).exists())

    def test_specialists_are_offered_it_first(self):
        specialist = free_interpreter(self.day, specialisations=["MEDICAL"])
        free_interpreter(self.day, specialisations=["GENERAL"])

        booking = self.book(session_type="medical")

        self.assertEqual(
            list(booking.offers.values_list("interpreter_id", flat=True)),
            [specialist.pk],
        )

    def test_everyone_free_is_offered_it_when_there_is_no_specialist(self):
        a, b = free_interpreter(self.day), free_interpreter(self.day)

        booking = self.book(session_type="legal")

        self.assertEqual(
            set(booking.offers.values_list("interpreter_id", flat=True)), {a.pk, b.pk}
        )

    def test_a_time_that_has_already_passed_cannot_be_booked(self):
        response = self.client.post(
            reverse("interpreters:schedule-session"),
            {
                "date": str(timezone.localdate()),
                "time": "00:00",
                "duration": 30,
                "session_type": "other",
                "custom_session_type": "x",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("time", response.data)


class AcceptingTests(SchedulingBase):
    def setUp(self):
        super().setUp()
        self.first, self.second = free_interpreter(self.day), free_interpreter(self.day)
        self.booking = self.book(session_type="other")

    def test_the_first_to_accept_gets_it_and_the_booker_is_told(self):
        response = self.answer(self.first, "accept", self.booking)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["my_state"], "confirmed")
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, Booking.Status.CONFIRMED)
        self.assertEqual(self.booking.interpreter, self.first)
        self.assertIsNotNone(self.booking.confirmed_at)
        note = self.notes(self.deaf, title="Your interpreter is confirmed").get()
        self.assertIn(self.first.user.full_name, note.body)

    def test_the_other_offers_close_and_cannot_be_accepted_late(self):
        self.answer(self.first, "accept", self.booking)

        late = self.answer(self.second, "accept", self.booking)

        self.assertEqual(late.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already", late.data["detail"])
        self.assertEqual(
            self.booking.offers.get(interpreter=self.second).outcome, Outcome.WITHDRAWN
        )
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.interpreter, self.first)

    def test_accepting_twice_changes_nothing(self):
        self.answer(self.first, "accept", self.booking)
        again = self.answer(self.first, "accept", self.booking)

        self.assertEqual(again.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self.notes(self.deaf, title="Your interpreter is confirmed").count(), 1
        )

    def test_someone_who_was_never_offered_it_cannot_accept(self):
        stranger = free_interpreter(self.day)
        # Offered to the first two only, as the stranger was created after the booking.
        response = self.answer(stranger, "accept", self.booking)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, Booking.Status.PENDING)

    def test_someone_who_declined_cannot_then_accept(self):
        self.answer(self.first, "decline", self.booking)

        response = self.answer(self.first, "accept", self.booking)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_clash_with_a_session_they_already_hold_is_refused(self):
        other = self.book(session_type="other", at=time(10, 15), day=self.day)
        scheduling.accept_offer(other.pk, self.first)
        # `first` was offered the booking before accepting the other one, so the offer still exists.
        response = self.answer(self.first, "accept", self.booking)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already have a session", response.data["detail"])

    def test_a_cancelled_booking_cannot_be_accepted(self):
        self.as_user(self.deaf)
        self.client.post(
            reverse("interpreters:booked-session-cancel", args=[self.booking.pk])
        )

        response = self.answer(self.first, "accept", self.booking)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("cancelled", response.data["detail"])

    def test_a_session_that_has_started_cannot_be_accepted(self):
        with self.assertRaises(scheduling.BookingNotAvailable):
            scheduling.accept_offer(
                self.booking.pk,
                self.first,
                now=self.booking.starts_at + timedelta(minutes=1),
            )

    def test_an_unknown_booking_is_a_404(self):
        self.as_user(self.first.user)

        response = self.client.post(
            reverse("interpreters:booked-session-accept", args=[9999])
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_only_interpreters_can_answer(self):
        self.as_user(self.deaf)

        response = self.client.post(
            reverse("interpreters:booked-session-accept", args=[self.booking.pk])
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class DecliningTests(SchedulingBase):
    def test_declining_keeps_it_with_the_others(self):
        first, second = free_interpreter(self.day), free_interpreter(self.day)
        booking = self.book(session_type="other")

        self.answer(first, "decline", booking)

        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.PENDING)
        self.assertEqual(
            booking.offers.get(interpreter=first).outcome, Outcome.DECLINED
        )
        self.assertEqual(
            booking.offers.get(interpreter=second).outcome, Outcome.PENDING
        )
        self.assertEqual(
            self.answer(first, "decline", booking).status_code, status.HTTP_200_OK
        )

    def test_when_everyone_declines_it_stays_pending_and_needs_attention(self):
        only = free_interpreter(self.day)
        booking = self.book(
            session_type="other", day=timezone.localdate() + timedelta(days=1)
        )
        InterpreterAvailability.objects.create(
            interpreter=only,
            start_at=local(booking.date, time(8, 0)),
            end_at=local(booking.date, time(17, 0)),
        )
        scheduling.offer_session(booking)

        self.answer(only, "decline", booking)

        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.PENDING)
        self.assertIn(booking.pk, [b.pk for b in scheduling.unconfirmed()])

    def test_declining_brings_in_interpreters_who_were_not_asked_yet(self):
        first = free_interpreter(self.day, specialisations=["MEDICAL"])
        booking = self.book(session_type="medical")
        self.assertEqual(booking.offers.count(), 1)  # specialists only
        later = free_interpreter(self.day)  # someone general becomes free

        self.answer(first, "decline", booking)

        self.assertEqual(booking.offers.get(interpreter=later).outcome, Outcome.PENDING)


class ReleasingTests(SchedulingBase):
    def test_giving_a_session_back_sends_it_out_again_and_tells_the_booker(self):
        keeper, other = free_interpreter(self.day), free_interpreter(self.day)
        booking = self.book(session_type="other")
        self.answer(keeper, "accept", booking)

        response = self.answer(keeper, "release", booking)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.PENDING)
        self.assertIsNone(booking.interpreter)
        self.assertEqual(booking.offers.get(interpreter=other).outcome, Outcome.PENDING)
        self.assertEqual(
            booking.offers.get(interpreter=keeper).outcome, Outcome.DECLINED
        )
        self.assertTrue(
            self.notes(
                self.deaf, title="Your interpreter can no longer make it"
            ).exists()
        )

    def test_only_the_confirmed_interpreter_can_give_it_back(self):
        keeper, other = free_interpreter(self.day), free_interpreter(self.day)
        booking = self.book(session_type="other")
        self.answer(keeper, "accept", booking)

        response = self.answer(other, "release", booking)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        booking.refresh_from_db()
        self.assertEqual(booking.interpreter, keeper)

    def test_a_session_that_has_started_cannot_be_given_back(self):
        keeper = free_interpreter(self.day)
        booking = self.book(session_type="other")
        scheduling.accept_offer(booking.pk, keeper)

        with self.assertRaises(scheduling.BookingNotAvailable):
            scheduling.release_session(
                booking.pk, keeper, now=booking.starts_at + timedelta(minutes=1)
            )


class CancellingTests(SchedulingBase):
    def cancel(self, booking, user=None):
        self.as_user(user or self.deaf)
        return self.client.post(
            reverse("interpreters:booked-session-cancel", args=[booking.pk])
        )

    def test_cancelling_closes_the_offers(self):
        profile = free_interpreter(self.day)
        booking = self.book(session_type="other")

        response = self.cancel(booking)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.CANCELLED)
        self.assertEqual(
            booking.offers.get(interpreter=profile).outcome, Outcome.WITHDRAWN
        )

    def test_cancelling_a_confirmed_session_tells_the_interpreter(self):
        profile = free_interpreter(self.day)
        booking = self.book(session_type="other")
        scheduling.accept_offer(booking.pk, profile)

        self.cancel(booking)

        self.assertTrue(
            self.notes(profile.user, title="A booked session was cancelled").exists()
        )

    def test_cancelling_twice_is_harmless(self):
        booking = self.book()
        self.cancel(booking)

        self.assertEqual(self.cancel(booking).status_code, status.HTTP_200_OK)

    def test_someone_else_cannot_cancel_it(self):
        booking = self.book()

        response = self.cancel(booking, user=make_user(User.Role.DEAF_HOH))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        booking.refresh_from_db()
        self.assertEqual(booking.status, Booking.Status.PENDING)

    def test_a_session_that_has_started_cannot_be_cancelled(self):
        booking = self.book()

        with self.assertRaises(scheduling.BookingNotAvailable):
            scheduling.cancel_session(
                booking.pk, self.deaf, now=booking.starts_at + timedelta(minutes=1)
            )


class WhatTheBookerSeesTests(SchedulingBase):
    def listed(self):
        self.as_user(self.deaf)
        return self.client.get(reverse("interpreters:booked-sessions")).data

    def test_the_interpreter_is_named_only_once_confirmed(self):
        profile = free_interpreter(self.day)
        booking = self.book(session_type="other")

        self.assertIsNone(self.listed()[0]["interpreter_name"])

        scheduling.accept_offer(booking.pk, profile)

        item = self.listed()[0]
        self.assertEqual(item["status"], "CONFIRMED")
        self.assertEqual(item["interpreter_name"], profile.user.full_name)

    def test_creating_a_booking_returns_no_interpreter_yet(self):
        response = self.client.post(
            reverse("interpreters:schedule-session"),
            {
                "date": str(self.day),
                "time": "10:00",
                "duration": 30,
                "session_type": "medical",
            },
            format="json",
        )

        self.assertIsNone(response.data["interpreter_name"])
        self.assertEqual(response.data["status"], "PENDING")


class SweepTests(SchedulingBase):
    def test_a_waiting_booking_is_offered_when_someone_becomes_free(self):
        booking = self.book(session_type="other")
        self.assertFalse(booking.offers.exists())
        late = free_interpreter(self.day)

        result = scheduling.sweep()

        self.assertEqual(result["scheduled_offered"], 1)
        self.assertEqual(booking.offers.get().interpreter, late)

    def test_a_second_sweep_does_not_offer_it_again(self):
        free_interpreter(self.day)
        self.book(session_type="other")

        self.assertEqual(scheduling.sweep()["scheduled_offered"], 0)
        self.assertEqual(
            self.notes(Booking.objects.get().offers.get().interpreter.user).count(), 1
        )

    def test_a_booking_with_an_open_offer_is_left_alone(self):
        free_interpreter(self.day)
        self.book(session_type="other")
        free_interpreter(self.day)

        self.assertEqual(scheduling.sweep()["scheduled_offered"], 0)


class AdminFollowUpTests(SchedulingBase):
    def test_an_admin_assignment_confirms_it_and_tells_everyone(self):
        a, b = free_interpreter(self.day), free_interpreter(self.day)
        booking = self.book(session_type="other")

        scheduling.assign_session(booking.pk, b)

        booking.refresh_from_db()
        self.assertEqual(
            (booking.status, booking.interpreter), (Booking.Status.CONFIRMED, b)
        )
        self.assertEqual(booking.offers.get(interpreter=a).outcome, Outcome.WITHDRAWN)
        self.assertTrue(
            self.notes(self.deaf, title="Your interpreter is confirmed").exists()
        )
        self.assertTrue(
            self.notes(b.user, title="You were assigned a session").exists()
        )

    def test_reassigning_tells_the_previous_interpreter(self):
        a, b = free_interpreter(self.day), free_interpreter(self.day)
        booking = self.book(session_type="other")
        scheduling.assign_session(booking.pk, a)

        scheduling.assign_session(booking.pk, b)

        self.assertTrue(
            self.notes(a.user, title="A booked session was reassigned").exists()
        )
        booking.refresh_from_db()
        self.assertEqual(booking.interpreter, b)

    def test_assigning_someone_who_is_not_approved_or_is_busy_is_refused(self):
        booking = self.book(session_type="other")
        unapproved = free_interpreter(self.day)
        unapproved.verification_status = VerificationStatus.INACTIVE
        unapproved.save()
        busy = free_interpreter(self.day)
        other = self.book(session_type="other", at=time(10, 15))
        scheduling.assign_session(other.pk, busy)

        for profile in (unapproved, busy):
            with self.assertRaises(scheduling.BookingNotAvailable):
                scheduling.assign_session(booking.pk, profile)

    def test_the_admin_page_assigns_through_the_same_rules(self):
        profile = free_interpreter(self.day)
        booking = self.book(session_type="other")
        model_admin = ScheduledSessionAdmin(Booking, site)
        request = RequestFactory().post("/")
        request.session = {}
        request._messages = FallbackStorage(request)
        form = SimpleNamespace(
            changed_data=["interpreter"], cleaned_data={"interpreter": profile}
        )

        model_admin.save_model(request, booking, form, change=True)

        booking.refresh_from_db()
        self.assertEqual(booking.interpreter, profile)
        self.assertEqual(booking.status, Booking.Status.CONFIRMED)

    def test_the_admin_page_cannot_clear_the_interpreter(self):
        profile = free_interpreter(self.day)
        booking = self.book(session_type="other")
        scheduling.assign_session(booking.pk, profile)
        model_admin = ScheduledSessionAdmin(Booking, site)
        request = RequestFactory().post("/")
        request.session = {}
        request._messages = FallbackStorage(request)
        form = SimpleNamespace(
            changed_data=["interpreter"], cleaned_data={"interpreter": None}
        )

        model_admin.save_model(request, booking, form, change=True)

        booking.refresh_from_db()
        self.assertEqual(booking.interpreter, profile)

    def test_the_admin_list_names_the_interpreter(self):
        profile = free_interpreter(self.day)
        booking = self.book(session_type="other")
        scheduling.assign_session(booking.pk, profile)
        admin_user = User.objects.create_user(
            "+256700999999",
            "Sign-Connect-2026",
            full_name="Admin",
            email="a@example.com",
            role=User.Role.ADMIN,
        )
        self.as_user(admin_user)

        rows = self.client.get(reverse("dashboard-admin-upcoming-sessions")).data

        self.assertEqual(rows[0]["interpreter"], profile.user.full_name)


class NeedsAttentionTests(SchedulingBase):
    def test_soon_or_long_waiting_unconfirmed_bookings_are_flagged(self):
        soon = self.book(day=timezone.localdate() + timedelta(days=1))
        far = self.book(day=timezone.localdate() + timedelta(days=20))
        old = self.book(day=timezone.localdate() + timedelta(days=21))
        Booking.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(hours=7)
        )

        flagged = {b.pk for b in scheduling.unconfirmed()}

        self.assertEqual(flagged, {soon.pk, old.pk})
        self.assertNotIn(far.pk, flagged)

    def test_confirmed_and_cancelled_ones_are_not_flagged(self):
        profile = free_interpreter(timezone.localdate() + timedelta(days=1))
        confirmed = self.book(
            session_type="other", day=timezone.localdate() + timedelta(days=1)
        )
        scheduling.accept_offer(confirmed.pk, profile)
        cancelled = self.book(day=timezone.localdate() + timedelta(days=1))
        scheduling.cancel_session(cancelled.pk, self.deaf)

        self.assertEqual(list(scheduling.unconfirmed()), [])


class ReminderTests(SchedulingBase):
    def setUp(self):
        super().setUp()
        self.profile = free_interpreter(self.day)
        self.booking = self.book(session_type="other")
        scheduling.accept_offer(self.booking.pk, self.profile)
        self.booking.refresh_from_db()
        self.start = self.booking.starts_at

    def reminders(self, user):
        return self.notes(user, data__reminder__isnull=False)

    def run_at(self, before):
        return scheduling.send_due_reminders(now=self.start - before)

    def test_nothing_goes_out_more_than_a_day_ahead(self):
        self.assertEqual(self.run_at(timedelta(hours=25)), 0)

    def test_both_people_get_the_day_reminder_once(self):
        self.assertEqual(self.run_at(timedelta(hours=23)), 2)
        self.assertEqual(self.run_at(timedelta(hours=22)), 0)

        booker = self.reminders(self.deaf).get()
        interpreter = self.reminders(self.profile.user).get()
        self.assertEqual(booker.data["reminder"], "day")
        self.assertIn(self.profile.user.full_name, booker.body)
        self.assertIn(self.deaf.full_name, interpreter.body)
        self.assertIn("10:00", booker.body)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.reminders_sent, ["day"])

    def test_the_short_reminder_follows_fifteen_minutes_before(self):
        self.run_at(timedelta(hours=23))

        self.assertEqual(self.run_at(timedelta(minutes=30)), 0)
        self.assertEqual(self.run_at(timedelta(minutes=10)), 2)
        self.assertEqual(self.run_at(timedelta(minutes=5)), 0)

        self.assertEqual(
            sorted(self.reminders(self.deaf).values_list("data__reminder", flat=True)),
            ["day", "soon"],
        )
        self.assertEqual(
            self.reminders(self.deaf).latest("id").title, "Your session starts soon"
        )

    def test_no_day_reminder_when_they_were_only_just_told(self):
        Booking.objects.filter(pk=self.booking.pk).update(
            confirmed_at=self.start - timedelta(hours=2)
        )

        self.assertEqual(self.run_at(timedelta(hours=1, minutes=30)), 0)
        self.assertEqual(self.run_at(timedelta(minutes=10)), 2)
        self.assertEqual(
            list(self.reminders(self.deaf).values_list("data__reminder", flat=True)),
            ["soon"],
        )

    def test_a_missed_day_reminder_is_not_sent_late(self):
        # The sweep was not running all day; with 40 minutes to go only the short one still matters.
        self.assertEqual(self.run_at(timedelta(minutes=40)), 0)
        self.assertEqual(self.run_at(timedelta(minutes=10)), 2)

        self.assertEqual(
            list(self.reminders(self.deaf).values_list("data__reminder", flat=True)),
            ["soon"],
        )

    def test_nothing_after_the_session_has_started(self):
        self.assertEqual(
            scheduling.send_due_reminders(now=self.start + timedelta(minutes=1)), 0
        )

    def test_cancelled_and_unconfirmed_sessions_get_no_reminders(self):
        waiting = self.book(session_type="other", at=time(14, 0))
        free_interpreter(self.day, opens=time(8, 0), closes=time(9, 0))
        scheduling.cancel_session(self.booking.pk, self.deaf)

        count = scheduling.send_due_reminders(
            now=waiting.starts_at - timedelta(minutes=10)
        )

        self.assertEqual(count, 0)
        self.assertFalse(self.reminders(self.deaf).exists())

    def test_a_new_interpreter_gets_their_own_reminders(self):
        replacement = free_interpreter(self.day)
        self.run_at(timedelta(hours=23))
        self.assertEqual(self.reminders(self.profile.user).count(), 1)

        scheduling.assign_session(self.booking.pk, replacement)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.reminders_sent, [])
        self.run_at(timedelta(hours=22))

        self.assertEqual(self.reminders(replacement.user).count(), 1)
        # The person who booked is reminded about the new arrangement as well.
        self.assertIn(
            replacement.user.full_name, self.reminders(self.deaf).latest("id").body
        )

    def test_a_released_session_is_not_reminded_until_it_has_someone_again(self):
        self.run_at(timedelta(hours=23))
        scheduling.release_session(self.booking.pk, self.profile)

        self.assertEqual(self.run_at(timedelta(minutes=10)), 0)

    def test_the_sweep_reports_how_many_reminders_it_sent(self):
        Booking.objects.filter(pk=self.booking.pk).update(
            date=timezone.localdate(),
            time=(timezone.localtime() + timedelta(minutes=10)).time(),
            confirmed_at=timezone.now() - timedelta(days=2),
        )
        self.booking.refresh_from_db()
        if self.booking.starts_at <= timezone.now():  # the clock crossed midnight
            self.skipTest("too close to midnight to place a session later today")

        result = scheduling.sweep()

        self.assertEqual(result["reminders_sent"], 2)


class StartingTheCallTests(SchedulingBase):
    def setUp(self):
        super().setUp()
        self.interpreter = free_interpreter(self.day)
        self.booking = self.book(session_type="other")
        scheduling.accept_offer(self.booking.pk, self.interpreter)
        self.booking.refresh_from_db()
        self.opens = self.booking.starts_at - scheduling.JOIN_EARLY

    def start(self, user):
        self.as_user(user)
        return self.client.post(
            reverse("interpreters:booked-session-start", args=[self.booking.pk])
        )

    def move_to_now(self, minutes=5):
        """Put the booking `minutes` from now, so the real clock is inside the join window."""
        soon = timezone.localtime() + timedelta(minutes=minutes)
        Booking.objects.filter(pk=self.booking.pk).update(
            date=soon.date(), time=soon.time().replace(second=0, microsecond=0)
        )
        self.booking.refresh_from_db()

    def test_the_booker_opens_a_call_with_the_confirmed_interpreter(self):
        call = scheduling.start_call(self.booking.pk, self.deaf, now=self.opens)

        self.assertEqual(call.status, "ASSIGNED")
        self.assertEqual(call.requester, self.deaf)
        self.assertEqual(call.interpreter, self.interpreter)
        self.assertEqual(call.video_session.status, "waiting")
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.service_request, call)

    def test_the_other_person_is_told_it_is_ready(self):
        call = scheduling.start_call(self.booking.pk, self.deaf, now=self.opens)

        note = self.notes(
            self.interpreter.user, title="Your session is ready to join"
        ).get()
        self.assertEqual(note.data["request_id"], call.pk)
        self.assertFalse(self.notes(self.deaf, title="Your session is ready to join"))

    def test_the_interpreter_can_open_it_too_and_the_booker_is_told(self):
        scheduling.start_call(self.booking.pk, self.interpreter.user, now=self.opens)

        self.assertTrue(self.notes(self.deaf, title="Your session is ready to join"))

    def test_the_second_person_gets_the_same_call(self):
        first = scheduling.start_call(self.booking.pk, self.deaf, now=self.opens)
        second = scheduling.start_call(
            self.booking.pk, self.interpreter.user, now=self.opens
        )

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            self.notes(
                self.interpreter.user, title="Your session is ready to join"
            ).count(),
            1,
        )

    def test_it_is_too_early_before_the_window(self):
        with self.assertRaises(scheduling.BookingNotAvailable) as caught:
            scheduling.start_call(
                self.booking.pk, self.deaf, now=self.opens - timedelta(minutes=1)
            )
        self.assertIn("10 minutes before", str(caught.exception))
        self.assertFalse(Booking.objects.get(pk=self.booking.pk).service_request)

    def test_it_is_too_late_after_the_session_is_over(self):
        late = self.booking.ends_at + scheduling.JOIN_LATE + timedelta(minutes=1)
        with self.assertRaises(scheduling.BookingNotAvailable):
            scheduling.start_call(self.booking.pk, self.deaf, now=late)

    def test_a_session_that_already_ended_cannot_be_opened_again(self):
        call = scheduling.start_call(self.booking.pk, self.deaf, now=self.opens)
        call.status = "COMPLETED"
        call.save()

        with self.assertRaises(scheduling.BookingNotAvailable):
            scheduling.start_call(self.booking.pk, self.deaf, now=self.opens)

    def test_nobody_else_can_open_it(self):
        stranger = make_user(User.Role.DEAF_HOH)
        with self.assertRaises(scheduling.BookingNotAvailable):
            scheduling.start_call(self.booking.pk, stranger, now=self.opens)

    def test_a_booking_with_no_interpreter_cannot_be_opened(self):
        pending = self.book(at=time(14, 0), session_type="other")
        with self.assertRaises(scheduling.BookingNotAvailable):
            scheduling.start_call(
                pending.pk, self.deaf, now=pending.starts_at - timedelta(minutes=5)
            )

    def test_the_interpreter_is_busy_while_the_call_runs(self):
        scheduling.start_call(self.booking.pk, self.deaf, now=self.opens)

        self.interpreter.refresh_from_db()
        self.assertFalse(self.interpreter.is_available)

    def test_the_endpoint_returns_the_request_to_join(self):
        self.move_to_now()

        response = self.start(self.deaf)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.booking.refresh_from_db()
        self.assertEqual(response.data["request_id"], self.booking.service_request_id)

    def test_the_endpoint_explains_why_it_is_too_early(self):
        response = self.start(self.deaf)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("10 minutes before", response.data["detail"])

    def test_a_stranger_gets_404(self):
        response = self.start(make_user(User.Role.DEAF_HOH))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @override_settings(
        LIVEKIT_URL="ws://livekit.test:7880",
        LIVEKIT_API_KEY="testkey",
        LIVEKIT_API_SECRET="s" * 40,
    )
    def test_both_can_get_a_call_token_for_the_opened_call(self):
        self.move_to_now()
        request_id = self.start(self.deaf).data["request_id"]

        for user in (self.deaf, self.interpreter.user):
            self.as_user(user)
            response = self.client.post(
                reverse("service-request-call-token", args=[request_id])
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

    def test_can_join_is_true_only_inside_the_window(self):
        self.as_user(self.deaf)
        listing = self.client.get(reverse("interpreters:booked-sessions"))
        self.assertFalse(listing.data[0]["can_join"])

        self.move_to_now()
        listing = self.client.get(reverse("interpreters:booked-sessions"))
        self.assertTrue(listing.data[0]["can_join"])

        self.as_user(self.interpreter.user)
        mine = self.client.get(reverse("interpreters:interpreter-booked-sessions"))
        self.assertTrue(mine.data[0]["can_join"])


class NoShowTests(SchedulingBase):
    def setUp(self):
        super().setUp()
        self.interpreter = free_interpreter(self.day)
        self.booking = self.book(session_type="other")
        scheduling.accept_offer(self.booking.pk, self.interpreter)
        self.booking.refresh_from_db()
        self.start = self.booking.starts_at
        self.late = self.start + scheduling.NO_SHOW_AFTER + timedelta(minutes=1)

    def open_call(self, *, interpreter_in=False, booker_in=False):
        call = scheduling.start_call(
            self.booking.pk, self.deaf, now=self.start - timedelta(minutes=5)
        )
        video = call.video_session
        if interpreter_in:
            video.interpreter_joined_at = self.start
        if booker_in:
            video.requester_joined_at = self.start
        video.save()
        return call

    def titles(self, user):
        return set(self.notes(user).values_list("title", flat=True))

    def test_nothing_happens_before_the_grace_period_ends(self):
        self.open_call()

        handled = scheduling.handle_no_shows(
            now=self.start + scheduling.NO_SHOW_AFTER - timedelta(seconds=1)
        )

        self.assertEqual(handled, 0)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.no_show, "")

    def test_nobody_opening_the_call_is_a_no_show_by_both(self):
        handled = scheduling.handle_no_shows(now=self.late)

        self.assertEqual(handled, 1)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.no_show, "both")
        self.assertIn("Your booked session was missed", self.titles(self.deaf))
        self.assertIn(
            "The booked session did not go ahead", self.titles(self.interpreter.user)
        )

    def test_a_missing_interpreter_is_told_and_the_booker_gets_a_replacement_request(
        self,
    ):
        replacement = free_interpreter(timezone.localdate(self.late))
        replacement.is_available = True
        replacement.save()
        call = self.open_call(booker_in=True)

        scheduling.handle_no_shows(now=self.late)

        self.booking.refresh_from_db()
        self.assertEqual(self.booking.no_show, "interpreter")
        call.refresh_from_db()
        self.assertEqual(call.status, "COMPLETED")
        self.assertIn("You missed a booked session", self.titles(self.interpreter.user))
        body = self.notes(self.deaf, title="Your booked session was missed").get().body
        self.assertIn("another one", body)
        from apps.service_requests.models import ServiceRequest

        self.assertTrue(
            ServiceRequest.objects.filter(requester=self.deaf)
            .exclude(pk=call.pk)
            .exists()
        )

    def test_a_missing_interpreter_with_no_booker_waiting_makes_no_replacement_request(
        self,
    ):
        from apps.service_requests.models import ServiceRequest

        self.open_call()

        scheduling.handle_no_shows(now=self.late)

        self.assertEqual(ServiceRequest.objects.filter(requester=self.deaf).count(), 1)

    def test_a_missing_booker_closes_the_call_and_frees_the_interpreter(self):
        call = self.open_call(interpreter_in=True)

        scheduling.handle_no_shows(now=self.late)

        self.booking.refresh_from_db()
        self.assertEqual(self.booking.no_show, "booker")
        call.refresh_from_db()
        self.assertEqual(call.status, "COMPLETED")
        self.interpreter.refresh_from_db()
        self.assertTrue(self.interpreter.is_available)
        self.assertIn(
            "The booked session did not go ahead", self.titles(self.interpreter.user)
        )
        self.assertNotIn(
            "You missed a booked session", self.titles(self.interpreter.user)
        )

    def test_a_session_where_both_joined_is_left_alone(self):
        self.open_call(interpreter_in=True, booker_in=True)

        self.assertEqual(scheduling.handle_no_shows(now=self.late), 0)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.no_show, "")

    def test_a_session_that_ended_normally_is_left_alone(self):
        call = self.open_call(interpreter_in=True)
        call.status = "COMPLETED"
        call.save()

        self.assertEqual(scheduling.handle_no_shows(now=self.late), 0)

    def test_it_is_dealt_with_once(self):
        scheduling.handle_no_shows(now=self.late)
        scheduling.handle_no_shows(now=self.late)

        self.assertEqual(
            self.notes(self.deaf, title="Your booked session was missed").count(), 1
        )

    def test_a_missed_session_cannot_be_opened_or_joined(self):
        scheduling.handle_no_shows(now=self.late)
        self.booking.refresh_from_db()

        self.assertFalse(scheduling.join_window_open(self.booking, now=self.late))
        with self.assertRaises(scheduling.BookingNotAvailable):
            scheduling.start_call(self.booking.pk, self.deaf, now=self.late)

    def test_the_sweep_handles_no_shows(self):
        with patch(
            "apps.interpreters.scheduling.timezone.localtime", return_value=self.late
        ):
            result = scheduling.sweep(now=self.late)

        self.assertEqual(result["no_shows"], 1)

    def test_a_booked_call_is_not_failed_by_the_waiting_timers(self):
        from apps.realtime.timeouts import fail_stale_calls

        call = self.open_call(booker_in=True)
        video = call.video_session
        video.status = "connecting"
        video.save()

        self.assertEqual(fail_stale_calls(now=self.start + timedelta(minutes=8)), 0)

    def test_the_interpreter_still_sees_a_session_that_has_started(self):
        soon = timezone.localtime() - timedelta(minutes=3)
        Booking.objects.filter(pk=self.booking.pk).update(
            date=soon.date(), time=soon.time().replace(second=0, microsecond=0)
        )
        self.as_user(self.interpreter.user)

        listing = self.client.get(reverse("interpreters:interpreter-booked-sessions"))

        self.assertEqual([row["id"] for row in listing.data], [self.booking.pk])
        self.assertTrue(listing.data[0]["can_join"])

    def test_the_booker_sees_what_went_wrong(self):
        scheduling.handle_no_shows(now=self.late)
        self.as_user(self.deaf)

        listing = self.client.get(reverse("interpreters:booked-sessions"))

        self.assertEqual(listing.data[0]["no_show"], "both")
