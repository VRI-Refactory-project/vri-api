from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("institutions", "0012_institutionprofile_rejection_reason_and_more")]

    operations = [
        migrations.AddField(
            model_name="institutionprofile",
            name="registration_number",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
    ]
