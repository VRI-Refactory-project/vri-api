from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("service_requests", "0003_close_duplicate_open_requests"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="servicerequest",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status__in", ["PENDING", "OFFERED"])),
                fields=("requester",),
                name="one_open_request_per_requester",
            ),
        ),
        migrations.AddConstraint(
            model_name="servicerequest",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status", "OFFERED")),
                fields=("interpreter",),
                name="one_offer_per_interpreter",
            ),
        ),
        migrations.AddConstraint(
            model_name="servicerequest",
            constraint=models.UniqueConstraint(
                condition=models.Q(("request_key__isnull", False)),
                fields=("requester", "request_key"),
                name="unique_request_key_per_requester",
            ),
        ),
    ]
