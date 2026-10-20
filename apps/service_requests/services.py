from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from apps.interpreters.services import get_eligible_interpreters
from apps.notifications.models import Notification
from apps.notifications.services import send_expo_push
from .models import SessionRequest, SessionRequestStatus

# How long a request may wait in the queue before it fails (Clarification C-11).
MAX_QUEUE_WAIT_SECONDS = getattr(settings, "MAX_QUEUE_WAIT_SECONDS", 300)

# An interpreter holding a request in one of these states is busy and must not be offered another one.
BUSY_STATUSES = [
    SessionRequestStatus.OFFERED,
    SessionRequestStatus.ACCEPTED,
    SessionRequestStatus.CONNECTING,
    SessionRequestStatus.ACTIVE,
]


def _is_busy(interpreter_user) -> bool:
    """True if this interpreter is currently holding an active or offered request."""
    return SessionRequest.objects.filter(
        offered_interpreter=interpreter_user,
        status__in=BUSY_STATUSES,
    ).exists()


def _is_eligible(interpreter_user) -> bool:
    """True if this interpreter is approved, switched on, and available right now."""
    return (
        get_eligible_interpreters(requested_at=timezone.now())
        .filter(user=interpreter_user)
        .exists()
    )


def _find_free_interpreter(session_request):
    """Finds an available interpreter profile for this request.

    First looks for a matching specialist; if none is available, falls back to an
    eligible general interpreter.
    """
    now = timezone.now()
    for specialisation in (session_request.specialisation, None):
        profile = (
            get_eligible_interpreters(
                specialisation_required=specialisation, requested_at=now
            )
            .exclude(user__offered_requests__status__in=BUSY_STATUSES)
            .order_by("pk")
            .first()
        )
        if profile:
            return profile
    return None


def _notify_interpreter(session_request, interpreter_user):
    """Helper function to create a database notification and trigger an Expo push

    notification after the database transaction successfully commits.
    """
    notification = Notification.objects.create(
        user=interpreter_user,
        title="New Interpreting Request",
        body="A new interpreting request has been offered to you. Open the app to respond.",
        category=Notification.Category.INCOMING_REQUEST,
        data={
            "request_id": session_request.pk,
            "is_emergency": session_request.is_emergency,
            "screen": "request-details",
        },
    )
    transaction.on_commit(lambda: send_expo_push(notification))


@transaction.atomic
def route_or_queue_request(session_request):
    """V2-023-BE-01: Routes the request to a free interpreter (OFFERED state).

    If no interpreter is immediately available, places the request in the QUEUED
    state.
    """
    profile = _find_free_interpreter(session_request)
    if profile:
        session_request.status = SessionRequestStatus.OFFERED
        session_request.offered_interpreter = profile.user
        session_request.offered_at = timezone.now()
        session_request.save(
            update_fields=[
                "status",
                "offered_interpreter",
                "offered_at",
                "updated_at",
            ]
        )
        _notify_interpreter(session_request, profile.user)
    else:
        session_request.status = SessionRequestStatus.QUEUED
        session_request.queued_at = timezone.now()
        session_request.save(update_fields=["status", "queued_at", "updated_at"])
    return session_request


@transaction.atomic
def create_session_request(
    *,
    requester,
    specialisation="general",
    description="",
    is_emergency=False,
    request_key=None,
    institution=None,
):
    """V2-022 / V2-024: Primary creation entry point for both Deaf users and

    institutional requesters. Supports idempotency retries via request_key and
    triggers immediate routing or queueing.
    """
    # Check for duplicate submission via request_key (idempotency safety)
    if request_key:
        existing = SessionRequest.objects.filter(
            requester=requester, request_key=request_key
        ).first()
        if existing:
            return existing

    session_request = SessionRequest.objects.create(
        requester=requester,
        requester_role=getattr(requester, "role", "deaf_hoh"),
        institution=institution,
        specialisation=specialisation,
        description=description,
        is_emergency=is_emergency,
        request_key=request_key,
        status=SessionRequestStatus.CREATED,
    )

    # Route immediately or queue
    return route_or_queue_request(session_request)


@transaction.atomic
def offer_next_queued_request(interpreter_user):
    """V2-023-BE-02: Pulls the highest-priority queued request for an interpreter

    becoming available. Emergency requests jump ahead first (-is_emergency),
    followed by FIFO submission time (created_at).
    """
    if _is_busy(interpreter_user) or not _is_eligible(interpreter_user):
        return None

    now = timezone.now()
    while True:
        # Use select_for_update(skip_locked=True) so parallel interpreters never race for the same request
        queued_request = (
            SessionRequest.objects.select_for_update(skip_locked=True)
            .filter(status=SessionRequestStatus.QUEUED)
            .order_by("-is_emergency", "created_at")
            .first()
        )

        if not queued_request:
            return None

        waited = (
            now - (queued_request.queued_at or queued_request.created_at)
        ).total_seconds()
        if waited > MAX_QUEUE_WAIT_SECONDS:
            queued_request.status = SessionRequestStatus.FAILED
            queued_request.failed_at = now
            queued_request.save(update_fields=["status", "failed_at", "updated_at"])
            continue  # Request timed out in queue; check the next one

        queued_request.status = SessionRequestStatus.OFFERED
        queued_request.offered_interpreter = interpreter_user
        queued_request.offered_at = now
        queued_request.save(
            update_fields=["status", "offered_interpreter", "offered_at", "updated_at"]
        )
        _notify_interpreter(queued_request, interpreter_user)
        return queued_request


def expire_stale_queued_requests() -> int:
    """Fails every request that has waited longer than MAX_QUEUE_WAIT_SECONDS.

    Can be run via Celery or management command on a 1-minute schedule. Returns
    failed count.
    """
    cutoff = timezone.now() - timedelta(seconds=MAX_QUEUE_WAIT_SECONDS)
    return SessionRequest.objects.filter(
        status=SessionRequestStatus.QUEUED, queued_at__lt=cutoff
    ).update(status=SessionRequestStatus.FAILED, failed_at=timezone.now())


def get_queue_position(session_request) -> int | None:
    """Calculates 1-based numerical position in queue (1 = next in line).

    Returns None if the request is not in QUEUED status.
    """
    if session_request.status != SessionRequestStatus.QUEUED:
        return None
    ahead = SessionRequest.objects.filter(status=SessionRequestStatus.QUEUED).filter(
        Q(is_emergency=True, is_emergency__gt=session_request.is_emergency)
        | Q(
            is_emergency=session_request.is_emergency,
            created_at__lt=session_request.created_at,
        )
    )
    return ahead.count() + 1


# Alias so both `get_queue_position` and `queue_position` work across serializers
queue_position = get_queue_position
