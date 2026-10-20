"""Creating, offering and cancelling service requests.

A new request is offered to one interpreter at a time. Accepting and declining an offer belong to
V2-023 and are not here yet.

There is no background worker (free hosting), so timeouts such as the requester-disconnect window
(V2-009) are applied whenever a request is read, and by ``sweep_requests`` for requests nobody reads.
"""

from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Max, Q
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
    Status.PENDING: {Status.OFFERED, Status.CANCELLED},
    Status.OFFERED: {Status.ASSIGNED, Status.OFFERED, Status.PENDING, Status.CANCELLED},
    Status.ASSIGNED: set(),
    Status.CANCELLED: set(),
}

# A silent requester only loses a request that is still waiting. Once it is offered, the
# disconnect rule no longer applies (V2-009 edge case 2).
DISCONNECT_CANCELLABLE = (Status.CREATED, Status.PENDING)


def check_transition(current, new):
    if new not in ALLOWED_TRANSITIONS[current]:
        raise InvalidTransition(f"A {current} request cannot become {new}.")


def offer_expiry_seconds():
    return getattr(settings, "REQUEST_OFFER_EXPIRY_SECONDS", 60)


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


def refresh_for_requester(service_request, now=None):
    """Bring a request up to date when its requester reads it, then note that they are still there."""
    now = now or timezone.now()
    cancel_if_requester_gone(service_request, now)
    if service_request.status in OPEN_STATUSES:
        ServiceRequest.objects.filter(pk=service_request.pk).update(last_seen_at=now)
        service_request.last_seen_at = now
    return service_request


def sweep_requests(now=None):
    """Apply timeouts to every open request. Safe to run repeatedly; returns what it changed."""
    now = now or timezone.now()
    stale = (
        ServiceRequest.objects.filter(status__in=DISCONNECT_CANCELLABLE)
        .filter(_requester_gone(disconnect_cutoff(now)))
        .select_related("requester")
    )
    cancelled = sum(cancel_if_requester_gone(service_request, now) for service_request in stale)
    return {"cancelled_requester_disconnected": cancelled}
