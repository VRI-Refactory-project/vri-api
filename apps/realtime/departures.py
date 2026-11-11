"""People who leave a call (V2-033).

When someone leaves, their departure is recorded and a grace window starts. Nothing ends straight away.

- They rejoin inside the window: the departure is resolved and the call carries on as before.
- The window ends and someone is still in the call: the call carries on for them.
- The window ends and nobody is left in the call: the session is settled.
  - It had connected: the session is ENDED and the request COMPLETED.
  - It never connected: the session is FAILED and the request COMPLETED (the apps show "could not connect").

A deliberate leave and a dropped connection are recorded differently, and only dropped connections are audited.
"""

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from livekit.protocol import models as livekit_models

from apps.audit.models import AuditLog
from apps.realtime.call_audit import record_call_event
from apps.realtime.events import broadcast_video_session_status
from apps.realtime.models import CallDeparture, VideoSession
from apps.service_requests.services import NotEndable, end_session


def departure_reason(participant):
    """LEFT when the person chose to leave. Anything else, including no reason from LiveKit, is DROPPED.

    DROPPED is the safe default: it gives the person the full grace window to come back.
    """
    reason = getattr(participant, "disconnect_reason", None)
    if reason == livekit_models.DisconnectReason.CLIENT_INITIATED:
        return CallDeparture.Reason.LEFT
    return CallDeparture.Reason.DROPPED


def record_departure(video_session, identity, reason, now):
    """Note that this person has left. Only one open departure per person; a repeat is ignored."""
    already_open = CallDeparture.objects.filter(
        video_session=video_session, identity=identity, settled_at__isnull=True
    ).exists()
    if already_open:
        return

    CallDeparture.objects.create(
        video_session=video_session,
        identity=identity,
        reason=reason,
        left_at=now,
    )
    if reason == CallDeparture.Reason.DROPPED:
        record_call_event(video_session, AuditLog.Event.CALL_DISCONNECTED)


def resolve_departure(video_session, identity, now):
    """The person has rejoined: their open departure is closed and the call carries on as before."""
    CallDeparture.objects.filter(
        video_session=video_session, identity=identity, settled_at__isnull=True
    ).update(settled_at=now)


def expire_departures(now=None):
    """Settle every session whose grace window has ended. Returns how many sessions were settled."""
    now = now or timezone.now()
    cutoff = now - timedelta(seconds=settings.SESSION_RECONNECT_GRACE_WINDOW_SECONDS)
    session_ids = list(
        CallDeparture.objects.filter(settled_at__isnull=True, left_at__lt=cutoff)
        .values_list("video_session_id", flat=True)
        .distinct()
    )

    settled = 0
    for session_id in session_ids:
        was_settled, request_to_complete = _settle_if_due(session_id, cutoff, now)
        settled += was_settled
        if request_to_complete is not None:
            _complete_request(request_to_complete)
    return settled


def _settle_if_due(session_id, cutoff, now):
    """Settle one session under a lock. Returns (was_settled, request_to_complete_or_None)."""
    with transaction.atomic():
        # Same row lock the webhook handler takes, so a rejoin and the settlement cannot interleave.
        video_session = VideoSession.objects.select_for_update().select_related("service_request").get(pk=session_id)
        due = list(video_session.departures.filter(settled_at__isnull=True, left_at__lt=cutoff))
        if not due:
            return False, None

        if video_session.joined_participants:
            # Someone is still in the call: it carries on for them. Only the grace window is closed.
            _close(due, now)
            return True, None

        if video_session.status not in (VideoSession.Status.ENDED, VideoSession.Status.FAILED):
            _finish_session(video_session, now)

        _close(due, now)
        return True, video_session.service_request


def _finish_session(video_session, now):
    """Nobody is left in the call: it is ENDED if it had connected, FAILED if it never did."""
    if video_session.connected_at:
        video_session.status = VideoSession.Status.ENDED
        record_call_event(video_session, AuditLog.Event.CALL_ENDED)
    else:
        video_session.status = VideoSession.Status.FAILED
        record_call_event(video_session, AuditLog.Event.CALL_FAILED)
    video_session.ended_at = video_session.ended_at or now
    video_session.save(update_fields=["status", "ended_at", "updated_at"])
    broadcast_video_session_status(video_session)


def _close(departures, now):
    CallDeparture.objects.filter(pk__in=[d.pk for d in departures]).update(settled_at=now)


def _complete_request(service_request):
    """The request becomes COMPLETED, the same as when the session is ended for everyone (V2-034).

    This runs outside the lock above, because end_session takes its own locks.
    """
    try:
        end_session(service_request)
    except NotEndable:
        pass  # already completed or cancelled: nothing more to do
