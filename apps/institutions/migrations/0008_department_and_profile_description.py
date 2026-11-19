import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("institutions", "0007_institutionprofile_verification_document")]

    operations = [
        migrations.AddField(
            model_name="institutionprofile",
            name="description",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.CreateModel(
            name="InstitutionDepartment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100)),
                ("is_active", models.BooleanField(default=True)),
                ("institution", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="departments", to="institutions.institutionprofile")),
            ],
            options={
                "ordering": ["name", "id"],
                "constraints": [models.UniqueConstraint(models.functions.Lower("name"), fields=("institution",), name="institution_department_name_ci_unique")],
            },
        ),
        migrations.AddField(
            model_name="institutionstaff",
            name="department",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="staff", to="institutions.institutiondepartment"),
        ),
    ]
