from django.db import migrations, models


class Migration(migrations.Migration):
    """Records every event the code uses: sign-in, institution decisions and staff changes."""

    dependencies = [
        ("audit", "0005_merge_audit_event_branches"),
    ]

    operations = [
        migrations.AlterField(
            model_name="auditlog",
            name="event",
            field=models.CharField(
                choices=[
                    ("admin_sign_in", "Administrator signed in"),
                    ("institution_approved", "Institution approved"),
                    ("institution_rejected", "Institution rejected"),
                    ("institution_deactivated", "Institution deactivated"),
                    ("staff_created", "Institution staff account created"),
                    ("staff_updated", "Institution staff account changed"),
                    ("staff_revoked", "Institution staff access revoked"),
                    ("staff_reinstated", "Institution staff access reinstated"),
                ],
                db_index=True,
                max_length=40,
            ),
        ),
    ]
