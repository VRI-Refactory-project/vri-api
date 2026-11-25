"""The administrator's live-session monitor (V2-047): the calls happening now, and which need a look.

Metadata only (BE-03): who is on a call, its type, channel, status and timings. Nothing said or signed in
a call, no chat, no request description and no feedback is ever part of a row. LIVE_ROW_FIELDS lists
the exact fields, and a test holds the row to it.

A call needs attention when any of these is true (the reasons in `needs_attention`):
- emergency: the request is an emergency;
- stuck: an on-demand call still waiting or connecting past half its time-out, so the admin hears about
  it before expire_stale_calls fails it (booked calls may open early and wait, so they are never stuck);
- over_duration: connected for longer than UNUSUAL_DURATION_THRESHOLD_MINUTES (BE-02);
- flagged: an administrator marked it for follow-up.

An administrator can mark the first three as seen (directly, or by marking a follow-up done). Seen
warnings stop counting and are listed under `seen` instead; a warning that appears afterwards shows.
"""

import logging
from datetime import timedelta

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone

from apps.realtime.models import SessionAcknowledgement, SessionFlag, VideoSession
from apps.service_requests.models import ServiceRequest

logger = logging.getLogger(__name__)
User = get_user_model()

LIVE_STATUSES = (
    VideoSession.Status.WAITING,
    VideoSession.Status.CONNECTING,
    VideoSession.Status.CONNECTED,
)
NOT_CONNECTED_YET = (VideoSession.Status.WAITING, VideoSession.Status.CONNECTING)

# Every administrator on the monitor page listens on this WebSocket group.
ADMIN_GROUP = "admin-live-sessions"

EMERGENCY = "emergency"
STUCK = "stuck"
OVER_DURATION = "over_duration"
FLAGGED = "flagged"
REASONS = (EMERGENCY, STUCK, OVER_DURATION, FLAGGED)
# Worked out from the call itself, so they can be marked as seen but not cleared.
AUTOMATIC_REASONS = (EMERGENCY, STUCK, OVER_DURATION)

LIVE_ROW_FIELDS = frozenset(
    {
        "id",
        "channel",
        "status",
        "status_label",
        "priority",
        "session_type",
        "sector",
        "requester",
        "interpreter",
        "created_at",
        "connected_at",
        "minutes_running",
        "needs_attention",
        "flag",
        "seen",
    }
)

ROLE_LABELS = {
    User.Role.DEAF_HOH: "Deaf user",
    User.Role.INSTITUTION: "Institution staff",
}


def with_row_data(queryset):
    """`queryset` of video sessions, with everything a row needs fetched in a few queries."""
    return queryset.select_related(
        "service_request__requester",
        "service_request__institution",
        "service_request__interpreter__user",
        "service_request__scheduled_session",
    ).prefetch_related(
        Prefetch(
            "flags",
            queryset=SessionFlag.objects.filter(cleared_at__isnull=True).select_related("flagged_by"),
            to_attr="open_flags",
        ),
        Prefetch(
            "acknowledgements",
            queryset=SessionAcknowledgement.objects.select_related("acknowledged_by"),
            to_attr="latest_acknowledgements",
        ),
    )


def live_calls():
    """The calls waiting, connecting or connected right now, newest first."""
    return with_row_data(VideoSession.objects.filter(status__in=LIVE_STATUSES)).order_by("-created_at")


def is_booked(service_request):
    return hasattr(service_request, "scheduled_session")


def first_join(call):
    joins = [t for t in (call.requester_joined_at, call.interpreter_joined_at) if t]
    return min(joins) if joins else None


def open_flag(call):
    flags = getattr(call, "open_flags", None)
    if flags is None:
        flags = list(call.flags.filter(cleared_at__isnull=True).select_related("flagged_by"))
    return flags[0] if flags else None


def latest_acknowledgement(call):
    acks = getattr(call, "latest_acknowledgements", None)
    if acks is None:
        acks = list(call.acknowledgements.select_related("acknowledged_by")[:1])
    return acks[0] if acks else None


def is_stuck(call, now):
    if is_booked(call.service_request):
        return False
    if call.status == VideoSession.Status.WAITING:
        return now - call.created_at > timedelta(seconds=settings.CALL_WAIT_TIMEOUT_SECONDS / 2)
    if call.status == VideoSession.Status.CONNECTING:
        joined = first_join(call)
        return joined is not None and now - joined > timedelta(seconds=settings.CALL_CONNECT_TIMEOUT_SECONDS / 2)
    return False


def is_over_duration(call, now):
    threshold = timedelta(minutes=settings.UNUSUAL_DURATION_THRESHOLD_MINUTES)
    return (
        call.status == VideoSession.Status.CONNECTED
        and call.connected_at is not None
        and now - call.connected_at > threshold
    )


def automatic_reasons(call, now):
    """The warnings that are true of the call right now, seen or not."""
    reasons = []
    if call.service_request.priority == ServiceRequest.Priority.EMERGENCY:
        reasons.append(EMERGENCY)
    if is_stuck(call, now):
        reasons.append(STUCK)
    if is_over_duration(call, now):
        reasons.append(OVER_DURATION)
    return reasons


def seen_reasons(call, now):
    """The warnings true right now that an administrator has already marked as seen."""
    ack = latest_acknowledgement(call)
    if ack is None:
        return []
    return [reason for reason in automatic_reasons(call, now) if reason in ack.reasons]


def attention_reasons(call, now=None):
    """Why this call needs an administrator's attention, in REASONS order. Empty when it is fine.

    Warnings already marked as seen are left out.
    """
    now = now or timezone.now()
    seen = seen_reasons(call, now)
    reasons = [reason for reason in automatic_reasons(call, now) if reason not in seen]
    if open_flag(call):
        reasons.append(FLAGGED)
    return reasons


def acknowledge(call, user, now=None):
    """Mark the call's current warnings as seen by `user`. Returns them (empty: nothing to mark)."""
    reasons = automatic_reasons(call, now or timezone.now())
    if reasons:
        SessionAcknowledgement.objects.create(video_session=call, reasons=reasons, acknowledged_by=user)
    return reasons


def _iso(value):
    return value.isoformat() if value else None


def live_row(call, now=None):
    """One row of the monitor. Metadata only: see the module docstring."""
    now = now or timezone.now()
    request = call.service_request
    requester = request.requester
    interpreter = request.interpreter
    flag = open_flag(call)
    seen = seen_reasons(call, now)
    ack = latest_acknowledgement(call) if seen else None
    started = call.connected_at or call.created_at
    return {
        "id": request.pk,
        "channel": "booked" if is_booked(request) else "on_demand",
        "status": call.status,
        "status_label": call.get_status_display(),
        "priority": request.priority,
        "session_type": request.specialisation_required,
        "sector": request.institution.organisation_name if request.institution_id else "Individual",
        "requester": {
            "name": requester.full_name,
            "role": requester.role,
            "role_label": ROLE_LABELS.get(requester.role, requester.get_role_display()),
        },
        "interpreter": {"name": interpreter.user.full_name} if interpreter else None,
        "created_at": _iso(call.created_at),
        "connected_at": _iso(call.connected_at),
        # Since the call connected; until then, how long it has been waiting.
        "minutes_running": max(0, int((now - started).total_seconds() // 60)),
        "needs_attention": attention_reasons(call, now),
        "flag": (
            {
                "flagged_by": flag.flagged_by.full_name if flag.flagged_by else "",
                "note": flag.note,
                "flagged_at": _iso(flag.flagged_at),
            }
            if flag
            else None
        ),
        # Warnings an administrator has marked as seen, which no longer count as needing attention.
        "seen": (
            {
                "seen_by": ack.acknowledged_by.full_name if ack.acknowledged_by else "",
                "seen_at": _iso(ack.acknowledged_at),
                "reasons": seen,
            }
            if ack
            else None
        ),
    }


def connecting_average_seconds(now=None):
    """The average time from the first person joining to the call connecting, over the last 24 hours."""
    now = now or timezone.now()
    calls = VideoSession.objects.filter(connected_at__gte=now - timedelta(hours=24)).only(
        "connected_at", "requester_joined_at", "interpreter_joined_at"
    )
    waits = []
    for call in calls:
        joined = first_join(call)
        if joined is not None and call.connected_at >= joined:
            waits.append((call.connected_at - joined).total_seconds())
    return round(sum(waits) / len(waits)) if waits else 0


def live_stats(now=None):
    """The figures at the top of the monitor."""
    now = now or timezone.now()
    calls = list(live_calls())
    reasons = [attention_reasons(call, now) for call in calls]
    return {
        "liveNow": sum(1 for call in calls if call.status == VideoSession.Status.CONNECTED),
        "connecting": sum(1 for call in calls if call.status in NOT_CONNECTED_YET),
        "connectingAverageSeconds": connecting_average_seconds(now),
        "needsAttention": sum(1 for found in reasons if found),
        "attention": {reason: sum(1 for found in reasons if reason in found) for reason in REASONS},
    }


def broadcast_live_session(video_session_id):
    """Push the call's current row to every administrator on the monitor (BE-01).

    It goes out once the current transaction commits, so admins never see a change that is rolled back.
    A row whose status is no longer live (ended, failed) tells the page to drop it.
    """

    def send():
        call = with_row_data(VideoSession.objects.filter(pk=video_session_id)).first()
        if call is None:
            return
        message = {"type": "live_session.updated", "session": live_row(call)}
        try:
            async_to_sync(get_channel_layer().group_send)(
                ADMIN_GROUP, {"type": "live.event", "message": message}
            )
        except Exception:  # the monitor must never break a call; the page also refreshes on its own
            logger.exception("Could not push live session %s to the admin monitor", video_session_id)

    transaction.on_commit(send)
