"""Creating, offering and cancelling service requests.

A new request is offered to one interpreter at a time. Accepting and declining an offer belong to
V2-023 and are not here yet.
"""

from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from apps.interpreters.routing import pick_interpreter
from apps.notifications.models import Notification
from apps.notifications.services import send_expo_push
from apps.service_requests.models import OPEN_STATUSES, RequestOffer, ServiceRequest


class OpenRequestExists(Exception):
    """The person already has a request that is still looking for an interpreter."""


class NotCancellable(Exception):
    """The request has already been accepted, so it can no longer be cancelled."""


def offer_expiry_seconds():
    return getattr(settings, "REQUEST_OFFER_EXPIRY_SECONDS", 60)


def create_request(
    *,
    requester,
    specialisation_required,
    description="",
    priority=ServiceRequest.Priority.NORMAL,
    request_key=None,
):
    """Make a request and try to offer it straight away.

    Returns ``(request, created)``. ``created`` is False when ``request_key`` matches a request this
    person already made, in which case that request is returned unchanged.
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

    offer_next(service_request)
    service_request.refresh_from_db()
    return service_request, True


def offer_next(service_request, *, now=None):
    """Offer a waiting request to the best available interpreter. Returns the offer, or None when
    nobody can take it right now and the request stays pending."""
    now = now or timezone.now()

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
