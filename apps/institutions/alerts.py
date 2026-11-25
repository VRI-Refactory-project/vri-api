"""Institution alert creation helpers shared by request, staff and queue flows."""

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


def no_interpreter_available(service_request):
    """Create or upgrade the one institution alert for a request that timed out."""
    if service_request.institution_id is None:
        return None
    return InstitutionAlert.objects.update_or_create(
        source_request_id=service_request.pk,
        defaults={
            "institution_id": service_request.institution_id,
            "title": "No Interpreter Available",
            "message": (
                f"A request for a {service_request.get_specialisation_required_display()} session "
                "has expired because no interpreter was available."
            ),
            "severity": InstitutionAlert.Severity.URGENT,
            "is_read": False,
            "created_at": service_request.failed_at,
        },
    )
