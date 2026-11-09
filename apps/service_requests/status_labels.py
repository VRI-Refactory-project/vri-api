"""V2-031: what each request state is called on screen.

TODO: replace the wording below with the UI Status Label Mapping table (Section 12 of the backlog).
Only this file holds wording, so changing a label never touches the sending code.
CONNECTING, ACTIVE and COMPLETED do not exist as request states yet; they are ready for when they do.
"""

STATUS_LABELS = {
    "CREATED": "Request sent",
    "PENDING": "Waiting for an interpreter",
    "OFFERED": "Finding an interpreter",
    "ASSIGNED": "Interpreter found",
    "CONNECTING": "Connecting",
    "ACTIVE": "Connected",
    "COMPLETED": "Session completed",
    "CANCELLED": "Session cancelled",
    "FAILED": "No interpreter available",
}


def label_for(status: str) -> str:
    """The on-screen label for a state. Never returns the raw internal name."""
    return STATUS_LABELS.get(str(status).upper(), "Updating")
