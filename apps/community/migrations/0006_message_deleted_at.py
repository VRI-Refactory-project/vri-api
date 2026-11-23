from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("community", "0005_message_file"),
    ]

    operations = [
        migrations.AddField(
            model_name="message",
            name="deleted_at",
            field=models.DateTimeField(
                blank=True,
                help_text="The sender took it back. Its text, sticker, photo and file are gone; the row stays so the chat shows it was deleted.",
                null=True,
            ),
        ),
    ]
