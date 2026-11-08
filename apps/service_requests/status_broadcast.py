"""V2-031: tell everyone in a session when its status changes.

Call `broadcast_status(service_request)` right after a request's status is saved.
`send_to_session` already waits for the database transaction to commit, so a change that is
rolled back is never announced.
"""

from apps.realtime.events import send_to_session

from .status_labels import label_for


def broadcast_status(service_request) -> None:
    status = service_request.status
    send_to_session(
        service_request.pk,
        "session.status",
        {"status": status, "label": label_for(status)},
    )
