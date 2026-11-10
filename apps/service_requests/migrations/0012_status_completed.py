from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('service_requests', '0011_servicerequest_ended_at_sessioninvitation'),
    ]

    operations = [
        migrations.AlterField(
            model_name='servicerequest',
            name='status',
            field=models.CharField(choices=[('CREATED', 'Created'), ('PENDING', 'Pending'), ('OFFERED', 'Offered'), ('ASSIGNED', 'Assigned'), ('COMPLETED', 'Completed'), ('CANCELLED', 'Cancelled'), ('FAILED', 'Failed')], db_index=True, default='PENDING', max_length=20),
        ),
    ]
