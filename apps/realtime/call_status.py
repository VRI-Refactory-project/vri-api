"""What the apps show for a request while its call is going on (V2-011, V2-033).

The request keeps its own status (ASSIGNED, COMPLETED, ...). For a request with a call, the apps need to show how
the call went, so this maps the call's status onto the request states the apps already know.
A call that FAILED is shown as FAILED even when the request is COMPLETED (V2-033 completes requests whose calls
never connected, so the request status alone would hide the failure).
"""

from apps.realtime.models import VideoSession
from apps.service_requests.models import ServiceRequest

CALL_STATUS_TO_REQUEST_STATE = {
    VideoSession.Status.WAITING: ServiceRequest.Status.ASSIGNED,
    VideoSession.Status.CONNECTING: "CONNECTING",
    VideoSession.Status.CONNECTED: "ACTIVE",
    VideoSession.Status.ENDED: ServiceRequest.Status.COMPLETED,
    VideoSession.Status.FAILED: ServiceRequest.Status.FAILED,
}


def display_status(service_request):
    """The status to show for this request."""
    video_session = VideoSession.objects.filter(service_request=service_request).first()
    if video_session is None:
        return service_request.status

    if video_session.status == VideoSession.Status.FAILED:
        return ServiceRequest.Status.FAILED

    if service_request.status != ServiceRequest.Status.ASSIGNED:
        return service_request.status

    return CALL_STATUS_TO_REQUEST_STATE[video_session.status]
