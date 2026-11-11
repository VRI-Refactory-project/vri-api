"""Audit entries for video calls (V2-011, BE-03).

Only the event, the request number and the call's status are recorded. Never media, chat or names.
"""

from apps.audit import services as audit


def record_call_event(video_session, event):
    audit.record(
        event=event,
        user=None,  # a system event, not something one person did
        details={
            "request_id": video_session.service_request_id,
            "status": video_session.status,
        },
    )
