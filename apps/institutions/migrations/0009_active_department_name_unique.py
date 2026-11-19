from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("institutions", "0008_department_and_profile_description")]

    operations = [
        migrations.RemoveConstraint(
            model_name="institutiondepartment",
            name="institution_department_name_ci_unique",
        ),
        migrations.AddConstraint(
            model_name="institutiondepartment",
            constraint=models.UniqueConstraint(
                models.functions.Lower("name"),
                models.F("institution"),
                condition=models.Q(is_active=True),
                name="institution_department_name_ci_unique",
            ),
        ),
    ]
