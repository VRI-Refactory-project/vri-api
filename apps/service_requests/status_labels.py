"""V2-031: what each request state is called on screen.

TODO: replace the wording below with the UI Status Label Mapping table (Section 12 of the backlog).
Only this file holds wording, so changing a label never touches the sending code.
CONNECTING and ACTIVE do not exist as request states yet; they are ready for when they do.
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


# What the push says for the states that send one (V2-036). The push title is the on-screen label above,
# so the two never disagree. TODO: replace these bodies with the approved wording, like the labels.
PUSH_BODIES = {
    "ASSIGNED": "An interpreter has accepted your request. Open the app to join.",
    "COMPLETED": "The session has ended.",
    "FAILED": "We couldn't find an interpreter in time. Please try again.",
}


def push_for(status: str):
    """The (title, body) of the push for a state, or None when that state sends no push."""
    body = PUSH_BODIES.get(str(status).upper())
    return (label_for(status), body) if body else None


def label_for(status: str) -> str:
    """The on-screen label for a state. Never returns the raw internal name."""
    return STATUS_LABELS.get(str(status).upper(), "Updating")
