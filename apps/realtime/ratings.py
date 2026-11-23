from django.contrib.auth import get_user_model
from django.db.models import F

from apps.realtime.models import SessionFeedback, VideoSession
from apps.service_requests.models import ServiceRequest

User = get_user_model()


def deaf_completed_session_feedback():
    """Feedback from Deaf requesters for calls completed with their assigned interpreter."""
    return SessionFeedback.objects.filter(
        submitted_by_id=F("video_session__service_request__requester_id"),
        submitted_by__role=User.Role.DEAF_HOH,
        interpreter_id=F("video_session__service_request__interpreter_id"),
        video_session__status=VideoSession.Status.ENDED,
        video_session__service_request__status=ServiceRequest.Status.COMPLETED,
    )
