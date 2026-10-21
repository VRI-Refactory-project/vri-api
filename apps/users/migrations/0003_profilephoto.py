import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import apps.users.models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0002_userprofile_communication_methods_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ProfilePhoto",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "token",
                    models.CharField(
                        default=apps.users.models.generate_photo_token,
                        editable=False,
                        max_length=32,
                        unique=True,
                    ),
                ),
                ("image", models.BinaryField()),
                ("content_type", models.CharField(default="image/jpeg", max_length=50)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="photo",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
    ]
