"""Fail calls that never connect (V2-011, RT-03).

A call is "connecting" from the moment the first person joins until both are in. If it stays that way
longer than CALL_CONNECT_TIMEOUT_SECONDS, it is marked failed and everyone on the session is told.
The management command expire_stale_calls runs this every minute.
"""

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.realtime.call_audit import record_call_event
from apps.realtime.events import broadcast_video_session_status, send_to_session
from apps.realtime.models import VideoSession


def fail_stale_calls(now=None):
    """Mark every call that has been connecting too long as failed. Returns how many were failed."""
    now = now or timezone.now()
    cutoff = now - timedelta(seconds=settings.CALL_CONNECT_TIMEOUT_SECONDS)

    candidate_ids = list(
        VideoSession.objects.filter(status=VideoSession.Status.CONNECTING).values_list("pk", flat=True)
    )
    return sum(1 for pk in candidate_ids if _fail_if_stale(pk, cutoff, now))


def _fail_if_stale(pk, cutoff, now):
    with transaction.atomic():
        # Lock the row and check again: someone may have joined since the query above. The webhook
        # handler locks the same row, so the two cannot change it at the same time.
        video_session = VideoSession.objects.select_for_update().get(pk=pk)
        if video_session.status != VideoSession.Status.CONNECTING or not _connecting_since_before(
            video_session, cutoff
        ):
            return False

        video_session.status = VideoSession.Status.FAILED
        video_session.ended_at = now
        video_session.save(update_fields=["status", "ended_at", "updated_at"])

        record_call_event(video_session, AuditLog.Event.CALL_FAILED)
        broadcast_video_session_status(video_session)
        send_to_session(video_session.service_request_id, "call.failed", {"reason": "connect_timeout"})
    return True


def _connecting_since_before(video_session, cutoff):
    """True when the first person joined before the cutoff."""
    joins = [t for t in (video_session.requester_joined_at, video_session.interpreter_joined_at) if t]
    return bool(joins) and min(joins) < cutoff
