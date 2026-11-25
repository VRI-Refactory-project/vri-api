from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("institutions", "0012_institutionprofile_rejection_reason_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="InstitutionAlert",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=150)),
                ("message", models.TextField()),
                ("severity", models.CharField(choices=[("INFO", "Info"), ("WARNING", "Warning"), ("URGENT", "Urgent")], max_length=10)),
                ("is_read", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("source_request_id", models.PositiveBigIntegerField(blank=True, null=True, unique=True)),
                ("institution", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="alerts", to="institutions.institutionprofile")),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
    ]
