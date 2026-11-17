import django.db.models.deletion
from django.db import migrations, models
from django.db.models import OuterRef, Subquery


def tag_existing_staff_requests(apps, schema_editor):
    """Requests made before V2-013 belong to the institution their requester is a staff member of."""
    ServiceRequest = apps.get_model("service_requests", "ServiceRequest")
    InstitutionStaff = apps.get_model("institutions", "InstitutionStaff")
    membership = InstitutionStaff.objects.filter(user_id=OuterRef("requester_id"))
    ServiceRequest.objects.filter(requester__institution_staff__isnull=False).update(
        institution_id=Subquery(membership.values("institution_id")[:1])
    )


class Migration(migrations.Migration):
    dependencies = [
        ("institutions", "0007_institutionprofile_verification_document"),
        ("service_requests", "0012_status_completed"),
    ]

    operations = [
        migrations.AddField(
            model_name="servicerequest",
            name="institution",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="service_requests",
                to="institutions.institutionprofile",
            ),
        ),
        # Going back only removes the column, so there is nothing to undo here.
        migrations.RunPython(tag_existing_staff_requests, migrations.RunPython.noop),
    ]
