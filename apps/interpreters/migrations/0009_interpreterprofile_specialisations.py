from django.db import migrations, models


def copy_single_specialisation(apps, schema_editor):
    """Give every existing interpreter a list that matches what they already had."""
    InterpreterProfile = apps.get_model("interpreters", "InterpreterProfile")
    for profile in InterpreterProfile.objects.all():
        profile.specialisations = [profile.specialisation]
        profile.save(update_fields=["specialisations"])


class Migration(migrations.Migration):

    dependencies = [
        ("interpreters", "0008_availabilityperiod"),
    ]

    operations = [
        migrations.AddField(
            model_name="interpreterprofile",
            name="specialisations",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Everything the interpreter covers, from the specialisation library (see specialisations.py). `specialisation` is the one used for routing.",
            ),
        ),
        migrations.RunPython(copy_single_specialisation, migrations.RunPython.noop),
    ]
