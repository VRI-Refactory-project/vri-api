import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('realtime', '0004_sessionfeedback'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='sessionfeedback',
            name='category',
            field=models.CharField(blank=True, choices=[('Audio', 'Audio'), ('Video', 'Video'), ('Other', 'Other')], default='', max_length=10),
        ),
        migrations.AddField(
            model_name='sessionfeedback',
            name='issues',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='sessionfeedback',
            name='comment',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AlterField(
            model_name='sessionfeedback',
            name='video_session',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='feedback', to='realtime.videosession'),
        ),
        migrations.AddConstraint(
            model_name='sessionfeedback',
            constraint=models.UniqueConstraint(fields=('video_session', 'submitted_by'), name='one_feedback_per_person_and_video_session'),
        ),
    ]
