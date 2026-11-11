"""Fail calls that never happen (V2-011, RT-03).

Two cases are failed:
- connect_timeout: someone joined, but the call did not connect within CALL_CONNECT_TIMEOUT_SECONDS of the
  first join.
- no_join: nobody joined within CALL_WAIT_TIMEOUT_SECONDS of the session being created.

Everyone on the session is told, and the failure is written to the audit log. The management command
expire_stale_calls runs this every minute.
"""

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.realtime.call_audit import record_call_event
from apps.realtime.events import broadcast_video_session_status, send_to_session
from apps.realtime.models import VideoSession

CONNECT_TIMEOUT = "connect_timeout"
NO_JOIN = "no_join"


def fail_stale_calls(now=None):
    """Mark every call that has gone stale as failed. Returns how many were failed."""
    now = now or timezone.now()
    candidate_ids = list(
        VideoSession.objects.filter(
            status__in=[VideoSession.Status.WAITING, VideoSession.Status.CONNECTING]
        ).values_list("pk", flat=True)
    )
    return sum(1 for pk in candidate_ids if _fail_if_stale(pk, now))


def _fail_if_stale(pk, now):
    with transaction.atomic():
        # Lock the row and check again: someone may have joined since the query above. The webhook
        # handler locks the same row, so the two cannot change it at the same time.
        video_session = VideoSession.objects.select_for_update().get(pk=pk)
        reason = _stale_reason(video_session, now)
        if reason is None:
            return False

        video_session.status = VideoSession.Status.FAILED
        video_session.ended_at = now
        video_session.save(update_fields=["status", "ended_at", "updated_at"])

        record_call_event(video_session, AuditLog.Event.CALL_FAILED)
        broadcast_video_session_status(video_session)
        send_to_session(video_session.service_request_id, "call.failed", {"reason": reason})
    return True


def _stale_reason(video_session, now):
    """Why this call should be failed, or None when it is still allowed to happen."""
    if video_session.status == VideoSession.Status.WAITING:
        cutoff = now - timedelta(seconds=settings.CALL_WAIT_TIMEOUT_SECONDS)
        return NO_JOIN if video_session.created_at < cutoff else None

    if video_session.status == VideoSession.Status.CONNECTING:
        first_join = _first_join(video_session)
        cutoff = now - timedelta(seconds=settings.CALL_CONNECT_TIMEOUT_SECONDS)
        return CONNECT_TIMEOUT if first_join is not None and first_join < cutoff else None

    return None  # connected, ended or already failed: never touched here


def _first_join(video_session):
    joins = [t for t in (video_session.requester_joined_at, video_session.interpreter_joined_at) if t]
    return min(joins) if joins else None
