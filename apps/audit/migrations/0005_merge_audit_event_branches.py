from django.db import migrations


class Migration(migrations.Migration):
    """Joins the two audit migration chains that started from 0001 (institution decision events
    and staff events), so Django has a single latest migration again."""

    dependencies = [
        ("audit", "0002_alter_auditlog_event"),
        ("audit", "0004_auditlog_target_user_staff_events"),
    ]

    operations = []
