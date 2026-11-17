from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from apps.audit.models import AuditLog

User = get_user_model()


@receiver(post_save, sender=User)
def log_new_user_registration(sender, instance, created, **kwargs):
    """Logs when a new user is created anywhere on the platform."""
    if created:
        AuditLog.objects.create(
            user=instance, 
            event=AuditLog.Event.USER_REGISTERED
        )
