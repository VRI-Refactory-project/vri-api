"""Booked (scheduled) sessions: offering them to interpreters, confirming them, and keeping track.

A booking starts PENDING with no interpreter. It is offered to every interpreter who is approved and
free for the whole slot (specialists first, everyone else only when no specialist is free). The first
one to accept gets it and the booking becomes CONFIRMED; the other offers close. If nobody takes it
the booking stays PENDING and shows up for an admin to assign by hand.

Interpreters' "switched on" dashboard toggle is about live requests right now, so it is not used
here: someone who is offline this minute can still take a booking for next week.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.notifications.models import Notification
from apps.notifications.services import create_notification

from .availability import hours_for
from .models import (
    InterpreterAvailability,
    InterpreterProfile,
    ScheduledSession,
    ScheduledSessionOffer,
    VerificationStatus,
)

logger = logging.getLogger(__name__)

Status = ScheduledSession.Status
Outcome = ScheduledSessionOffer.Outcome

# What each kind of booking needs, in the specialisation library's terms (see specialisations.py).
SPECIALISATION_FOR = {
    "medical": "MEDICAL",
    "legal": "LEGAL",
    "education": "EDUCATION",
    "social": "SOCIAL_SERVICES",
    "media": "MEDIA",
    "conferences": "EVENTS",
    "religious": "RELIGIOUS",
    "police": "SECURITY",
    "other": "GENERAL",
}


# The call can be opened this long before the start and until this long after the planned end.
JOIN_EARLY = timedelta(minutes=10)
JOIN_LATE = timedelta(minutes=15)
# Someone who has not joined this long after the start is counted as not coming.
NO_SHOW_AFTER = timedelta(minutes=10)


class BookingNotAvailable(Exception):
    """The booking cannot be answered or changed any more. The message is safe to show the user."""


def attention_after_hours():
    return getattr(settings, "SCHEDULED_ATTENTION_HOURS", 6)


def _label(session):
    """e.g. "Medical / Healthcare", or the person's own words for Other."""
    if (
        session.session_type == ScheduledSession.SessionType.OTHER
        and session.custom_session_type
    ):
        return session.custom_session_type
    return session.get_session_type_display()


def _when(session):
    return session.starts_at.strftime("%a %d %b at %H:%M")


def overlaps(session, other):
    return session.starts_at < other.ends_at and other.starts_at < session.ends_at


def ensure_no_clash(user, day, at, duration):
    """A person cannot book two sessions that overlap: pressing Save twice makes one booking.

    Cancelled bookings do not count. Raises BookingNotAvailable with the message to show.
    """
    wanted = ScheduledSession(date=day, time=at, duration=duration)
    around = ScheduledSession.objects.filter(
        deaf_user=user,
        status__in=[Status.PENDING, Status.CONFIRMED],
        date__in=[day - timedelta(days=1), day, day + timedelta(days=1)],
    )
    if any(overlaps(wanted, other) for other in around):
        raise BookingNotAvailable("You already have a session booked at that time.")


def _confirmed_around(session, profile):
    """The interpreter's confirmed bookings on the days this one touches."""
    days = {session.starts_at.date(), session.ends_at.date()}
    return ScheduledSession.objects.filter(
        interpreter=profile, status=Status.CONFIRMED, date__in=days
    ).exclude(pk=session.pk)


def is_free_for(profile, session):
    """True when the interpreter works through the whole slot and has no other confirmed booking in it."""
    start, end = session.starts_at, session.ends_at
    saved = InterpreterAvailability.objects.filter(interpreter=profile)
    if saved.exists():
        # Saved availability periods decide, as they do for live requests.
        covered = saved.filter(start_at__lte=start, end_at__gte=end).exists()
    else:
        works, opens, closes = hours_for(profile, start.date())
        covered = bool(
            works
            and opens
            and closes
            and end.date() == start.date()
            and opens <= start.time()
            and end.time() <= closes
        )
    if not covered:
        return False
    return not any(
        overlaps(session, other) for other in _confirmed_around(session, profile)
    )


def candidates_for(session, *, exclude=()):
    """Everyone who should be offered this booking: specialists first, otherwise everyone free."""
    profiles = (
        InterpreterProfile.objects.filter(
            verification_status=VerificationStatus.APPROVED, user__is_active=True
        )
        .exclude(pk__in=set(exclude))
        .exclude(user_id=session.deaf_user_id)
        .select_related("user")
    )
    free = [p for p in profiles if is_free_for(p, session)]
    wanted = SPECIALISATION_FOR.get(session.session_type, "GENERAL")
    if wanted == "GENERAL":
        return free
    specialists = [p for p in free if p.has_specialisation(wanted)]
    return specialists or free


def offer_session(session, *, now=None):
    """Offer a PENDING booking to everyone who can take it and has not yet been asked.

    Safe to call again: interpreters who already have an open offer are left alone, ones who
    declined are not asked twice, and ones whose offer was closed (someone else took it, then let go)
    are asked again. Returns the interpreters newly offered the booking."""
    now = now or timezone.now()
    if session.status != Status.PENDING or session.starts_at <= now:
        return []

    existing = {o.interpreter_id: o for o in session.offers.all()}
    skip = {
        pk
        for pk, o in existing.items()
        if o.outcome in (Outcome.PENDING, Outcome.DECLINED, Outcome.ACCEPTED)
    }
    newly = []
    with transaction.atomic():
        for profile in candidates_for(session, exclude=skip):
            offer = existing.get(profile.pk)
            if offer is None:
                ScheduledSessionOffer.objects.create(
                    session=session, interpreter=profile, offered_at=now
                )
            else:
                offer.outcome = Outcome.PENDING
                offer.offered_at = now
                offer.responded_at = None
                offer.save(update_fields=["outcome", "offered_at", "responded_at"])
            newly.append(profile)
            create_notification(
                user=profile.user,
                title="New booked session",
                body=f"{_label(session)} on {_when(session)}. Open the app to accept or decline.",
                category=Notification.Category.INCOMING_REQUEST,
                data={"session_id": session.pk, "screen": "booked-session"},
            )
        if newly and session.offered_at is None:
            session.offered_at = now
            session.save(update_fields=["offered_at", "updated_at"])
    return newly


def _withdraw_open_offers(session, *, keep=None):
    session.offers.filter(outcome=Outcome.PENDING).exclude(interpreter=keep).update(
        outcome=Outcome.WITHDRAWN
    )


def _confirm(session, profile, now):
    session.interpreter = profile
    session.status = Status.CONFIRMED
    session.confirmed_at = now
    # A new interpreter starts again, so they get their own reminders.
    session.reminders_sent = []
    session.save(
        update_fields=[
            "interpreter",
            "status",
            "confirmed_at",
            "reminders_sent",
            "updated_at",
        ]
    )
    _withdraw_open_offers(session, keep=profile)
    create_notification(
        user=session.deaf_user,
        title="Your interpreter is confirmed",
        body=f"{profile.user.full_name} will interpret your {_label(session)} session on {_when(session)}.",
        category=Notification.Category.GENERAL,
        data={"session_id": session.pk, "screen": "booked-session"},
    )


def accept_offer(session_id, profile, *, now=None):
    """The interpreter takes the booking. Giving the same answer twice changes nothing."""
    now = now or timezone.now()
    with transaction.atomic():
        session = (
            ScheduledSession.objects.select_for_update(of=("self",))
            .select_related("deaf_user")
            .get(pk=session_id)
        )
        if session.status == Status.CANCELLED:
            raise BookingNotAvailable("This booking was cancelled.")
        if session.status == Status.CONFIRMED:
            if session.interpreter_id == profile.pk:
                return session
            raise BookingNotAvailable("Someone else has already taken this booking.")
        if session.starts_at <= now:
            raise BookingNotAvailable("This session has already started.")
        offer = session.offers.filter(interpreter=profile).first()
        if offer is None or offer.outcome != Outcome.PENDING:
            raise BookingNotAvailable(
                "This booking was not offered to you, or you already answered it."
            )
        if any(
            overlaps(session, other) for other in _confirmed_around(session, profile)
        ):
            raise BookingNotAvailable("You already have a session at that time.")
        offer.outcome = Outcome.ACCEPTED
        offer.responded_at = now
        offer.save(update_fields=["outcome", "responded_at"])
        _confirm(session, profile, now)
    return session


def decline_offer(session_id, profile, *, now=None):
    """The interpreter says no. If everyone has now said no, others who were not asked get the offer."""
    now = now or timezone.now()
    with transaction.atomic():
        session = ScheduledSession.objects.select_for_update(of=("self",)).get(
            pk=session_id
        )
        offer = session.offers.filter(interpreter=profile).first()
        if offer is None:
            raise BookingNotAvailable("This booking was not offered to you.")
        if offer.outcome == Outcome.DECLINED:
            return session
        if offer.outcome != Outcome.PENDING or session.status != Status.PENDING:
            raise BookingNotAvailable("This booking can no longer be answered.")
        offer.outcome = Outcome.DECLINED
        offer.responded_at = now
        offer.save(update_fields=["outcome", "responded_at"])
        if not session.offers.filter(outcome=Outcome.PENDING).exists():
            offer_session(session, now=now)
    return session


def release_session(session_id, profile, *, now=None):
    """A confirmed interpreter can no longer make it: the booking goes back out to the others."""
    now = now or timezone.now()
    with transaction.atomic():
        session = (
            ScheduledSession.objects.select_for_update(of=("self",))
            .select_related("deaf_user")
            .get(pk=session_id)
        )
        if session.status != Status.CONFIRMED or session.interpreter_id != profile.pk:
            raise BookingNotAvailable("This is not one of your confirmed sessions.")
        if session.starts_at <= now:
            raise BookingNotAvailable("This session has already started.")
        session.offers.filter(interpreter=profile).update(
            outcome=Outcome.DECLINED, responded_at=now
        )
        session.interpreter = None
        session.status = Status.PENDING
        session.confirmed_at = None
        session.reminders_sent = []
        session.save(
            update_fields=[
                "interpreter",
                "status",
                "confirmed_at",
                "reminders_sent",
                "updated_at",
            ]
        )
        create_notification(
            user=session.deaf_user,
            title="Your interpreter can no longer make it",
            body=f"We are finding another interpreter for your {_label(session)} session on {_when(session)}.",
            category=Notification.Category.GENERAL,
            data={"session_id": session.pk, "screen": "booked-session"},
        )
        offer_session(session, now=now)
    return session


def join_window_open(session, now=None):
    """True when the booking is confirmed and it is time to open its call."""
    now = now or timezone.now()
    return (
        session.status == Status.CONFIRMED
        and session.interpreter_id is not None
        and not session.no_show
        and session.starts_at - JOIN_EARLY <= now <= session.ends_at + JOIN_LATE
    )


def start_call(session_id, user, *, now=None):
    """Open the call for a confirmed booking and return the live request both people join.

    Either the person who booked or the confirmed interpreter can do it, from ten minutes before the
    start. The first one makes the call; the second gets the same one, so there is never a second room.
    The other person is told it is ready.
    """
    from apps.realtime.events import broadcast_video_session_status
    from apps.realtime.models import VideoSession
    from apps.service_requests.models import ServiceRequest

    now = now or timezone.now()
    with transaction.atomic():
        session = (
            ScheduledSession.objects.select_for_update(of=("self",))
            .select_related("deaf_user", "interpreter__user", "service_request")
            .get(pk=session_id)
        )
        is_booker = session.deaf_user_id == user.pk
        is_interpreter = (
            session.interpreter_id is not None
            and session.interpreter.user_id == user.pk
        )
        if not (is_booker or is_interpreter):
            raise BookingNotAvailable("This is not your session.")
        if session.status != Status.CONFIRMED or session.interpreter_id is None:
            raise BookingNotAvailable("This session has no interpreter confirmed yet.")
        if session.no_show:
            raise BookingNotAvailable("This session was missed.")
        if now < session.starts_at - JOIN_EARLY:
            raise BookingNotAvailable(
                f"The call opens 10 minutes before the start, at {(session.starts_at - JOIN_EARLY).strftime('%H:%M')}."
            )
        call = session.service_request
        if call is not None and call.status == ServiceRequest.Status.ASSIGNED:
            return call
        if call is not None or now > session.ends_at + JOIN_LATE:
            raise BookingNotAvailable("This session is over.")

        call = ServiceRequest.objects.create(
            requester=session.deaf_user,
            institution=session.institution,
            interpreter=session.interpreter,
            description=f"Booked session: {_label(session)}",
            status=ServiceRequest.Status.ASSIGNED,
            assigned_at=now,
        )
        session.service_request = call
        session.save(update_fields=["service_request", "updated_at"])
        video_session = VideoSession.objects.create(
            service_request=call, status=VideoSession.Status.WAITING
        )
        broadcast_video_session_status(video_session)
        InterpreterProfile.objects.filter(pk=session.interpreter_id).update(
            is_available=False
        )
        other = session.interpreter.user if is_booker else session.deaf_user
        create_notification(
            user=other,
            title="Your session is ready to join",
            body=f"The {_label(session)} session is starting. Open it to join the call.",
            category=Notification.Category.GENERAL,
            data={
                "session_id": session.pk,
                "request_id": call.pk,
                "screen": "booked-session",
            },
        )
    return call


def cancel_session(session_id, user, *, now=None):
    """The person who booked takes the booking back."""
    now = now or timezone.now()
    with transaction.atomic():
        session = (
            ScheduledSession.objects.select_for_update(of=("self",))
            .select_related("interpreter__user")
            .get(pk=session_id)
        )
        if session.deaf_user_id != user.pk:
            raise BookingNotAvailable("This is not your booking.")
        if session.status == Status.CANCELLED:
            return session
        if session.starts_at <= now:
            raise BookingNotAvailable("This session has already started.")
        interpreter = session.interpreter
        session.status = Status.CANCELLED
        session.save(update_fields=["status", "updated_at"])
        _withdraw_open_offers(session)
        if interpreter is not None:
            create_notification(
                user=interpreter.user,
                title="A booked session was cancelled",
                body=f"The {_label(session)} session on {_when(session)} was cancelled.",
                category=Notification.Category.GENERAL,
                data={"session_id": session.pk, "screen": "booked-session"},
            )
    return session


def assign_session(session_id, profile, *, now=None):
    """An admin puts a specific interpreter on a booking (for ones nobody took)."""
    now = now or timezone.now()
    with transaction.atomic():
        session = (
            ScheduledSession.objects.select_for_update(of=("self",))
            .select_related("deaf_user")
            .get(pk=session_id)
        )
        if session.status == Status.CANCELLED:
            raise BookingNotAvailable("This booking was cancelled.")
        if session.starts_at <= now:
            raise BookingNotAvailable("This session has already started.")
        if profile.verification_status != VerificationStatus.APPROVED:
            raise BookingNotAvailable("That interpreter is not approved.")
        if session.interpreter_id == profile.pk:
            return session
        if any(
            overlaps(session, other) for other in _confirmed_around(session, profile)
        ):
            raise BookingNotAvailable(
                "That interpreter already has a session at that time."
            )
        previous = session.interpreter
        offer, _ = ScheduledSessionOffer.objects.get_or_create(
            session=session, interpreter=profile, defaults={"offered_at": now}
        )
        offer.outcome = Outcome.ACCEPTED
        offer.responded_at = now
        offer.save(update_fields=["outcome", "responded_at"])
        _confirm(session, profile, now)
        if previous is not None:
            create_notification(
                user=previous.user,
                title="A booked session was reassigned",
                body=f"The {_label(session)} session on {_when(session)} is now with someone else.",
                category=Notification.Category.GENERAL,
                data={"session_id": session.pk, "screen": "booked-session"},
            )
        create_notification(
            user=profile.user,
            title="You were assigned a session",
            body=f"{_label(session)} on {_when(session)}.",
            category=Notification.Category.GENERAL,
            data={"session_id": session.pk, "screen": "booked-session"},
        )
    return session


def upcoming(queryset, now=None):
    """Bookings that have not started yet (date and time in local time)."""
    now = timezone.localtime(now or timezone.now())
    return queryset.filter(
        Q(date__gt=now.date()) | Q(date=now.date(), time__gt=now.time())
    )


def unconfirmed(now=None):
    """Bookings still without an interpreter that someone should look at: they have waited a while
    or start within a day."""
    now = now or timezone.now()
    local = timezone.localtime(now)
    waiting = upcoming(ScheduledSession.objects.filter(status=Status.PENDING), now)
    return waiting.filter(
        Q(created_at__lte=now - timedelta(hours=attention_after_hours()))
        | Q(date__lte=local.date() + timedelta(days=1))
    )


# (name, how long before the start it is sent). The "day" one is skipped when the session was only
# confirmed inside that window, because the person has just been told.
REMINDERS = (
    ("day", timedelta(hours=24)),
    ("soon", timedelta(minutes=15)),
)


def _reminder_text(name, session, for_booker):
    when = timezone.localtime(session.starts_at).strftime("%H:%M")
    if name == "day":
        day = timezone.localtime(session.starts_at).date()
        word = "Tomorrow" if day != timezone.localdate() else "Today"
        head = f"{word} at {when}"
        title = "Session reminder: " + head.lower()
    else:
        head = f"Starting at {when}"
        title = "Your session starts soon"
    if for_booker:
        who = f"with {session.interpreter.user.full_name}"
    else:
        who = f"for {session.deaf_user.full_name}"
    return title, f"{head}: {_label(session)} session {who}."


def send_due_reminders(*, now=None):
    """Remind both people about confirmed sessions that are coming up. Each reminder goes out once.
    Returns how many reminders were sent (each person counts)."""
    now = now or timezone.now()
    sent = 0
    ahead = max(delay for _, delay in REMINDERS)
    candidates = upcoming(
        ScheduledSession.objects.filter(
            status=Status.CONFIRMED, interpreter__isnull=False
        ),
        now,
    ).filter(date__lte=timezone.localtime(now).date() + timedelta(days=2))
    for session_id in [
        pk
        for pk, start in ((c.pk, c.starts_at) for c in candidates)
        if start - now <= ahead
    ]:
        with transaction.atomic():
            session = (
                ScheduledSession.objects.select_for_update(of=("self",))
                .select_related("deaf_user", "interpreter__user")
                .get(pk=session_id)
            )
            if session.status != Status.CONFIRMED or session.interpreter_id is None:
                continue
            remaining = session.starts_at - now
            if remaining <= timedelta(0):
                continue
            done = list(session.reminders_sent)
            for name, delay in REMINDERS:
                if name in done or remaining > delay:
                    continue
                done.append(name)
                # No "day" reminder when the person was only just told, or when it is too late to
                # be useful because the session is less than an hour away.
                skip = name == "day" and (
                    remaining < timedelta(hours=1)
                    or (
                        session.confirmed_at is not None
                        and session.starts_at - session.confirmed_at <= delay
                    )
                )
                if skip:
                    continue
                for user, for_booker in (
                    (session.deaf_user, True),
                    (session.interpreter.user, False),
                ):
                    title, body = _reminder_text(name, session, for_booker)
                    create_notification(
                        user=user,
                        title=title,
                        body=body,
                        category=Notification.Category.GENERAL,
                        data={
                            "session_id": session.pk,
                            "screen": "booked-session",
                            "reminder": name,
                        },
                    )
                    sent += 1
            if done != session.reminders_sent:
                session.reminders_sent = done
                session.save(update_fields=["reminders_sent", "updated_at"])
    return sent


def _joined(call):
    """(interpreter has joined, booker has joined) for a call opened on a booking."""
    video = getattr(call, "video_session", None) if call else None
    if video is None:
        return False, False
    return (
        video.interpreter_joined_at is not None,
        video.requester_joined_at is not None,
    )


def _replacement_specialisation(session):
    from .models import Specialisation

    return {
        "medical": Specialisation.MEDICAL,
        "legal": Specialisation.LEGAL,
    }.get(session.session_type, Specialisation.GENERAL)


def handle_no_shows(*, now=None):
    """Deal with confirmed bookings where someone has not turned up ten minutes after the start.

    Nobody joined: both are told and the booking is marked missed. Only the interpreter is missing:
    the call is closed, the interpreter is told, and (when the booker is waiting) a live request for
    another interpreter is made for them straight away. Only the booker is missing: the call is
    closed, the interpreter is freed, and both are told. Each booking is dealt with once.
    Returns how many were handled.
    """
    from django.conf import settings

    from apps.service_requests import services as requests
    from apps.service_requests.models import ServiceRequest

    if not getattr(settings, "BOOKED_SESSION_NO_SHOW", True):
        return 0
    now = now or timezone.localtime()
    today = timezone.localdate(now)
    candidates = ScheduledSession.objects.filter(
        status=Status.CONFIRMED,
        no_show="",
        interpreter__isnull=False,
        date__in=[today - timedelta(days=1), today],
    )
    handled = 0
    for candidate in candidates:
        if candidate.starts_at + NO_SHOW_AFTER > now:
            continue
        with transaction.atomic():
            session = (
                ScheduledSession.objects.select_for_update(of=("self",))
                .select_related("deaf_user", "interpreter__user", "service_request")
                .get(pk=candidate.pk)
            )
            if session.no_show or session.status != Status.CONFIRMED:
                continue
            call = session.service_request
            if call is not None and call.status == ServiceRequest.Status.COMPLETED:
                continue  # it took place and was ended normally
            interpreter_in, booker_in = _joined(call)
            if interpreter_in and booker_in:
                continue
            if interpreter_in:
                who = ScheduledSession.NoShow.BOOKER
            elif booker_in:
                who = ScheduledSession.NoShow.INTERPRETER
            else:
                who = ScheduledSession.NoShow.BOTH

            if call is not None and call.status == ServiceRequest.Status.ASSIGNED:
                try:
                    requests.end_session(call, None)
                except requests.NotEndable:
                    pass
            session.no_show = who
            session.save(update_fields=["no_show", "updated_at"])
            label, when = _label(session), _when(session)
            data = {"session_id": session.pk, "screen": "booked-session"}

            if who != ScheduledSession.NoShow.BOOKER:
                create_notification(
                    user=session.interpreter.user,
                    title="You missed a booked session",
                    body=f"The {label} session on {when} started without you.",
                    category=Notification.Category.GENERAL,
                    data=data,
                )
            if who != ScheduledSession.NoShow.INTERPRETER:
                create_notification(
                    user=session.interpreter.user,
                    title="The booked session did not go ahead",
                    body=f"The person who booked the {label} session on {when} did not join.",
                    category=Notification.Category.GENERAL,
                    data=data,
                )
            if who == ScheduledSession.NoShow.INTERPRETER:
                body = (
                    f"Your interpreter did not join the {label} session on {when}. "
                    "We are looking for another one now."
                )
            elif who == ScheduledSession.NoShow.BOOKER:
                body = (
                    f"You did not join the {label} session on {when}, so it was closed."
                )
            else:
                body = f"Nobody joined the {label} session on {when}, so it was closed."
            create_notification(
                user=session.deaf_user,
                title="Your booked session was missed",
                body=body,
                category=Notification.Category.GENERAL,
                data=data,
            )
            logger.warning("Booked session %s no-show: %s", session.pk, who)

        if who == ScheduledSession.NoShow.INTERPRETER and now <= session.ends_at:
            try:
                requests.create_request(
                    requester=session.deaf_user,
                    specialisation_required=_replacement_specialisation(session),
                    description=f"Replacement for booked session: {label}",
                )
            except Exception:  # an open request already, or the emergency limit: the booker is told above
                logger.exception(
                    "Could not find a replacement for booked session %s", session.pk
                )
        handled += 1
    return handled


def sweep(*, now=None):
    """Offer waiting bookings to anyone newly free (for example an interpreter who has just been
    approved or has added availability), and send the reminders that are due. Run with the
    request-queue sweep. Reminders are only as punctual as the sweep is frequent."""
    now = now or timezone.now()
    offered = 0
    for session in upcoming(
        ScheduledSession.objects.filter(status=Status.PENDING), now
    ).select_related("deaf_user"):
        if session.offers.filter(outcome=Outcome.PENDING).exists():
            continue
        offered += len(offer_session(session, now=now))
    return {
        "scheduled_offered": offered,
        "reminders_sent": send_due_reminders(now=now),
        "no_shows": handle_no_shows(now=now),
    }
