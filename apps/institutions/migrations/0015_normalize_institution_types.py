from django.db import migrations


INSTITUTION_TYPES = (
    "HOSPITAL",
    "COURT",
    "GOVERNMENT",
    "SCHOOL",
    "POLICE",
    "CHURCH",
    "OTHER",
)


def normalize_organisation_types(apps, schema_editor):
    InstitutionProfile = apps.get_model("institutions", "InstitutionProfile")
    for value in INSTITUTION_TYPES:
        InstitutionProfile.objects.filter(organisation_type__iexact=value).exclude(
            organisation_type=value
        ).update(organisation_type=value)


class Migration(migrations.Migration):
    dependencies = [
        ("institutions", "0014_merge_20261125_institution_migrations"),
    ]

    operations = [
        migrations.RunPython(
            normalize_organisation_types,
            migrations.RunPython.noop,
        ),
    ]
