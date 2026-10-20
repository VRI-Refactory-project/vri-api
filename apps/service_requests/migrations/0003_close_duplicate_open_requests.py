from django.db import migrations
from django.utils import timezone


def close_duplicate_open_requests(apps, schema_editor):
    """Before each person is limited to one open request, keep their newest one and cancel the rest.

    The earlier version let a person make several pending requests, and the new database rule would
    refuse to be added while such rows exist.
    """
    ServiceRequest = apps.get_model("service_requests", "ServiceRequest")
    now = timezone.now()
    seen = set()
    open_requests = ServiceRequest.objects.filter(status="PENDING").order_by(
        "-created_at", "-id"
    )
    for service_request in open_requests:
        if service_request.requester_id in seen:
            service_request.status = "CANCELLED"
            service_request.cancelled_at = now
            service_request.save(update_fields=["status", "cancelled_at"])
        seen.add(service_request.requester_id)


class Migration(migrations.Migration):
    dependencies = [
        ("service_requests", "0002_offers_priority_and_constraints"),
    ]

    operations = [
        migrations.RunPython(close_duplicate_open_requests, migrations.RunPython.noop),
    ]
