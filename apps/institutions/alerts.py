"""Institution alert creation helpers shared by request, staff and queue flows."""

from datetime import timedelta

from django.utils import timezone

from apps.service_requests.models import ServiceRequest

from .models import InstitutionAlert, InstitutionStaff


def emergency_request_started(service_request):
    institution_id = service_request.institution_id
    if institution_id is None:
        return None
    membership = InstitutionStaff.objects.select_related("department").filter(
        user_id=service_request.requester_id
    ).first()
    department = membership.department.name if membership and membership.department else "No department"
    return InstitutionAlert.objects.create(
        institution_id=institution_id,
        title="Emergency Request Started",
        message=f"A staff member in the {department} department just started an Emergency VRI session.",
        severity=InstitutionAlert.Severity.URGENT,
    )


def staff_member_added(membership):
    department = membership.department.name if membership.department else "No department"
    return InstitutionAlert.objects.create(
        institution=membership.institution,
        title="New Staff Created",
        message=f"{membership.user.full_name} was added to the {department} department.",
        severity=InstitutionAlert.Severity.INFO,
    )


def alert_overdue_pending_requests(*, now=None):
    """Create one warning per institutional request once it has waited ten minutes."""
    now = now or timezone.now()
    cutoff = now - timedelta(minutes=10)
    count = 0
    waiting = ServiceRequest.objects.filter(
        institution__isnull=False,
        status=ServiceRequest.Status.PENDING,
        created_at__lte=cutoff,
    ).select_related("institution")
    for request in waiting.iterator():
        _, created = InstitutionAlert.objects.get_or_create(
            source_request_id=request.pk,
            defaults={
                "institution_id": request.institution_id,
                "title": "No Interpreter Available",
                "message": (
                    f"A request for a {request.get_specialisation_required_display()} session "
                    "has been waiting for over 10 minutes with no interpreter assigned."
                ),
                "severity": InstitutionAlert.Severity.WARNING,
            },
        )
        count += int(created)
    return count
