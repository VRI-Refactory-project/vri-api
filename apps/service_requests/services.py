"""Creating, offering and cancelling service requests.

A new request is offered to one interpreter at a time. Accepting and declining an offer belong to
V2-023 and are not here yet.

There is no background worker (free hosting), so timeouts such as the requester-disconnect window
(V2-009) are applied whenever a request is read, and by ``sweep`` for requests nobody reads.
"""

from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Case, IntegerField, Max, Q, Value, When
from django.utils import timezone

from apps.audit import services as audit
from apps.audit.models import AuditLog
from apps.interpreters.models import Specialisation
from apps.interpreters.routing import pick_interpreter
from apps.notifications.models import Notification
from apps.notifications.services import send_expo_push
from apps.service_requests.models import OPEN_STATUSES, RequestOffer, ServiceRequest


class OpenRequestExists(Exception):
    """The person already has a request that is still looking for an interpreter."""


class NotCancellable(Exception):
    """The request has already been accepted, so it can no longer be cancelled."""


class InvalidTransition(Exception):
    """A request was asked to move to a status it cannot reach from where it is."""


Status = ServiceRequest.Status

# The moves a request can make (V2-009 BE-03). Anything else is refused by ``check_transition``.
ALLOWED_TRANSITIONS = {
    Status.CREATED: {Status.PENDING, Status.OFFERED, Status.CANCELLED},
    Status.PENDING: {Status.OFFERED, Status.CANCELLED, Status.FAILED},
    Status.OFFERED: {Status.ASSIGNED, Status.OFFERED, Status.PENDING, Status.CANCELLED},
    Status.ASSIGNED: set(),
    Status.CANCELLED: set(),
    Status.FAILED: set(),
}

# A silent requester only loses a request that is still waiting. Once it is offered, the
# disconnect rule no longer applies (V2-009 edge case 2).
DISCONNECT_CANCELLABLE = (Status.CREATED, Status.PENDING)


def check_transition(current, new):
    if new not in ALLOWED_TRANSITIONS[current]:
        raise InvalidTransition(f"A {current} request cannot become {new}.")


def offer_expiry_seconds():
    """How long an interpreter has to answer an offer."""
    return getattr(settings, "REQUEST_OFFER_EXPIRY_SECONDS", 60)


def max_wait_seconds():
    """How long a request may wait for an interpreter before it is marked failed."""
    return getattr(settings, "REQUEST_MAX_WAIT_SECONDS", 300)


def create_request(
    *,
    requester,
    specialisation_required=Specialisation.GENERAL,
    description="",
    priority=ServiceRequest.Priority.NORMAL,
    request_key=None,
    request=None,
):
    """Make a request (CREATED) and try to offer it straight away, leaving it OFFERED or PENDING.

    Returns ``(request, created)``. ``created`` is False when ``request_key`` matches a request this
    person already made, in which case that request is returned unchanged.
    ``request`` is the HTTP request, used only for the audit entry's IP address.
    """
    if request_key:
        existing = ServiceRequest.objects.filter(
            requester=requester, request_key=request_key
        ).first()
        if existing:
            return existing, False

    # An old request that has run out of time must not stop the person asking again.
    for open_request in ServiceRequest.objects.filter(
        requester=requester, status__in=OPEN_STATUSES
    ):
        refresh_request(open_request)

    try:
        with transaction.atomic():
            service_request = ServiceRequest.objects.create(
                requester=requester,
                specialisation_required=specialisation_required,
                description=description,
                priority=priority,
                request_key=request_key or None,
                status=Status.CREATED,
            )
            audit.record(
                AuditLog.Event.REQUEST_CREATED,
                requester,
                request,
                details={
                    "request_id": service_request.pk,
                    "context": specialisation_required,
                    "priority": priority,
                    "requester_role": requester.role,
                },
            )
    except IntegrityError:
        # Either they already have an open request, or two copies of the same call arrived together.
        if request_key:
            existing = ServiceRequest.objects.filter(
                requester=requester, request_key=request_key
            ).first()
            if existing:
                return existing, False
        raise OpenRequestExists from None

    if offer_next(service_request) is None:
        check_transition(service_request.status, Status.PENDING)
        service_request.status = Status.PENDING
        service_request.save(update_fields=["status", "updated_at"])
    service_request.refresh_from_db()
    return service_request, True


def offer_next(service_request, *, now=None):
    """Offer a waiting request to the best available interpreter. Returns the offer, or None when
    nobody can take it right now and the request stays pending."""
    now = now or timezone.now()
    check_transition(service_request.status, Status.OFFERED)

    already_asked = set(service_request.offers.values_list("interpreter_id", flat=True))
    holding_an_offer = set(
        ServiceRequest.objects.filter(
            status=ServiceRequest.Status.OFFERED, interpreter__isnull=False
        ).values_list("interpreter_id", flat=True)
    )
    last_offered = dict(
        RequestOffer.objects.values("interpreter_id")
        .annotate(last=Max("offered_at"))
        .values_list("interpreter_id", "last")
    )

    match = pick_interpreter(
        service_request.specialisation_required,
        exclude=already_asked | holding_an_offer,
        last_offered=last_offered,
        now=now,
    )
    if match is None:
        return None

    try:
        with transaction.atomic():
            service_request.status = ServiceRequest.Status.OFFERED
            service_request.interpreter = match.interpreter
            service_request.offered_at = now
            service_request.matched_by_fallback = match.matched_by_fallback
            service_request.save()
            offer = RequestOffer.objects.create(
                service_request=service_request,
                interpreter=match.interpreter,
                offered_at=now,
                expires_at=now + timedelta(seconds=offer_expiry_seconds()),
            )
            notification = Notification.objects.create(
                user=match.interpreter.user,
                title="New interpreting request",
                body=(
                    "A new interpreting request has been offered to you. "
                    "Open the app to review it."
                ),
                category=Notification.Category.INCOMING_REQUEST,
                data={
                    "request_id": service_request.pk,
                    "screen": "request-details",
                    "context": service_request.specialisation_required,
                    "priority": service_request.priority,
                },
            )
            audit.record(
                AuditLog.Event.REQUEST_OFFERED,
                None,
                target_user=match.interpreter.user,
                details={
                    "request_id": service_request.pk,
                    "requester_id": service_request.requester_id,
                    "matched_by_fallback": match.matched_by_fallback,
                },
            )
            # Send the push only after the offer and notification are committed.
            transaction.on_commit(lambda: send_expo_push(notification))
    except IntegrityError:
        # Another request took this interpreter a moment ago. This one stays pending.
        service_request.refresh_from_db()
        return None

    return offer


def cancel_request(service_request):
    """Cancel an open request. Cancelling twice is fine and returns the same result."""
    now = timezone.now()
    with transaction.atomic():
        locked = ServiceRequest.objects.select_for_update().get(pk=service_request.pk)
        if locked.status == ServiceRequest.Status.CANCELLED:
            return locked
        if locked.status not in OPEN_STATUSES:
            raise NotCancellable
        locked.status = ServiceRequest.Status.CANCELLED
        locked.cancelled_at = now
        locked.interpreter = None
        locked.save()
        locked.offers.filter(outcome=RequestOffer.Outcome.PENDING).update(
            outcome=RequestOffer.Outcome.WITHDRAWN, responded_at=now
        )
    return locked


def disconnect_cutoff(now=None):
    return (now or timezone.now()) - settings.REQUESTER_DISCONNECT_GRACE_WINDOW


def _requester_gone(cutoff):
    # A request the app has never checked on counts from when it was made.
    return Q(last_seen_at__lt=cutoff) | Q(last_seen_at__isnull=True, created_at__lt=cutoff)


def cancel_if_requester_gone(service_request, now=None):
    """Cancel a waiting request whose requester has been silent past the grace window (V2-009 BE-05).

    Safe to call any number of times, and at the same time as other callers: the update only applies
    while the request still qualifies, so it happens once. Returns True when this call cancelled it.
    """
    now = now or timezone.now()
    cancelled = (
        ServiceRequest.objects.filter(pk=service_request.pk, status__in=DISCONNECT_CANCELLABLE)
        .filter(_requester_gone(disconnect_cutoff(now)))
        .update(
            status=Status.CANCELLED,
            cancelled_at=now,
            cancel_reason=ServiceRequest.CancelReason.REQUESTER_DISCONNECTED,
            updated_at=now,
        )
    )
    if not cancelled:
        return False
    audit.record(
        AuditLog.Event.REQUEST_CANCELLED,
        None,
        target_user=service_request.requester,
        details={
            "request_id": service_request.pk,
            "reason": ServiceRequest.CancelReason.REQUESTER_DISCONNECTED,
        },
    )
    service_request.refresh_from_db()
    return True


def cancel_disconnected_requests(*, now=None):
    """Cancel every waiting request whose requester has gone quiet. Returns how many."""
    now = now or timezone.now()
    stale = (
        ServiceRequest.objects.filter(status__in=DISCONNECT_CANCELLABLE)
        .filter(_requester_gone(disconnect_cutoff(now)))
        .select_related("requester")
    )
    return sum(cancel_if_requester_gone(service_request, now) for service_request in stale)


def refresh_for_requester(service_request, *, now=None):
    """What the requester's app gets when it checks on a request: the request is brought up to date
    (using when they were last seen), and only then does this check count as "still here"."""
    now = now or timezone.now()
    refresh_request(service_request, now=now)
    if service_request.status in OPEN_STATUSES:
        ServiceRequest.objects.filter(pk=service_request.pk).update(last_seen_at=now)
        service_request.last_seen_at = now
    return service_request


def expire_stale_offers(*, now=None, service_request=None, interpreter_user=None):
    """Take back offers nobody answered in time. Each such request goes back to pending, and the
    interpreter is never offered that request again. Returns how many offers expired."""
    now = now or timezone.now()
    stale = RequestOffer.objects.filter(
        outcome=RequestOffer.Outcome.PENDING,
        expires_at__lte=now,
        service_request__status=ServiceRequest.Status.OFFERED,
    )
    if service_request is not None:
        stale = stale.filter(service_request=service_request)
    if interpreter_user is not None:
        stale = stale.filter(interpreter__user=interpreter_user)

    expired = 0
    for offer in stale:
        with transaction.atomic():
            locked = ServiceRequest.objects.select_for_update().get(
                pk=offer.service_request_id
            )
            # Look again now the row is locked: it may have been accepted or cancelled meanwhile.
            if (
                locked.status != ServiceRequest.Status.OFFERED
                or locked.interpreter_id != offer.interpreter_id
            ):
                continue
            RequestOffer.objects.filter(pk=offer.pk).update(
                outcome=RequestOffer.Outcome.EXPIRED, responded_at=now
            )
            locked.status = ServiceRequest.Status.PENDING
            locked.interpreter = None
            locked.offered_at = None
            locked.matched_by_fallback = False
            locked.save()
            expired += 1
    return expired


def fail_overdue_requests(*, now=None, service_request=None):
    """Mark requests that have waited too long as failed. Returns how many."""
    now = now or timezone.now()
    cutoff = now - timedelta(seconds=max_wait_seconds())
    overdue = ServiceRequest.objects.filter(
        status=ServiceRequest.Status.PENDING, created_at__lte=cutoff
    )
    if service_request is not None:
        overdue = overdue.filter(pk=service_request.pk)

    failed = 0
    for found in overdue:
        with transaction.atomic():
            locked = ServiceRequest.objects.select_for_update().get(pk=found.pk)
            if locked.status != ServiceRequest.Status.PENDING:
                continue
            locked.status = ServiceRequest.Status.FAILED
            locked.failed_at = now
            locked.failure_reason = "no_interpreter_available"
            locked.save()
            failed += 1
    return failed


def dispatch_pending(*, now=None):
    """Offer every waiting request to an interpreter who is now free. Emergencies go first, then the
    request that has waited longest. Returns how many were offered."""
    waiting = ServiceRequest.objects.filter(
        status=ServiceRequest.Status.PENDING
    ).order_by(
        Case(
            When(priority=ServiceRequest.Priority.EMERGENCY, then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        ),
        "created_at",
        "id",
    )
    offered = 0
    for found in waiting:
        if offer_next(found, now=now) is not None:
            offered += 1
    return offered


def refresh_request(service_request, *, now=None):
    """Bring one request up to date: expire its offer if unanswered, give up if it has waited too
    long, and otherwise look for another interpreter. The app polling for status calls this, so the
    queue keeps moving without a background worker. A waiting request whose requester has gone quiet
    is cancelled before it is offered again (V2-009); one that has run out of time fails first."""
    now = now or timezone.now()
    expire_stale_offers(now=now, service_request=service_request)
    service_request.refresh_from_db()
    if service_request.status == ServiceRequest.Status.PENDING:
        fail_overdue_requests(now=now, service_request=service_request)
        service_request.refresh_from_db()
    cancel_if_requester_gone(service_request, now)
    if service_request.status == ServiceRequest.Status.PENDING:
        offer_next(service_request, now=now)
        service_request.refresh_from_db()
    return service_request


def sweep(*, now=None):
    """Move the whole queue forward once. Meant to be run every minute or so, by the
    `dispatch_requests` command or the health check."""
    now = now or timezone.now()
    expired = expire_stale_offers(now=now)
    # Requests that have waited too long fail first: the person has almost certainly given up, so
    # an interpreter should not be asked to take one.
    failed = fail_overdue_requests(now=now)
    # Then waiting requests whose requester's app has gone quiet are cancelled (V2-009).
    cancel_disconnected_requests(now=now)
    offered = dispatch_pending(now=now)
    return {"expired": expired, "offered": offered, "failed": failed}
