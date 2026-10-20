
from django.db import transaction
from django.utils import timezone

from apps.interpreters.services import get_eligible_interpreters
from apps.notifications.models import Notification
from apps.notifications.services import send_expo_push
from apps.service_requests.models import ServiceRequest


@transaction.atomic
def create_and_assign_request(
    *,
    requester,
    specialisation_required,
    description="",
):
    """Create a request and assign an eligible interpreter when available."""
    service_request = ServiceRequest.objects.create(
        requester=requester,
        specialisation_required=specialisation_required,
        description=description,
    )

    interpreter = (
        get_eligible_interpreters(
            specialisation_required=specialisation_required,
        )
        .order_by("pk")
        .first()
    )

    if interpreter is None:
        return service_request

    service_request.interpreter = interpreter
    service_request.status = ServiceRequest.Status.ASSIGNED
    service_request.assigned_at = timezone.now()
    service_request.save(
        update_fields=[
            "interpreter",
            "status",
            "assigned_at",
            "updated_at",
        ]
    )

    notification = Notification.objects.create(
        user=interpreter.user,
        title="New interpreting request",
        body=(
            "A new interpreting request has been assigned to you. "
            "Open the app to review it."
        ),
        category=Notification.Category.INCOMING_REQUEST,
        data={
            "request_id": service_request.pk,
            "screen": "request-details",
        },
    )

    # Send the push only after the request and notification are committed.
    transaction.on_commit(
        lambda: send_expo_push(notification)
    )

    return service_request
