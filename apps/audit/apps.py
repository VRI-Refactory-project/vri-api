# from django.apps import AppConfig


# class AuditConfig(AppConfig):
#     default_auto_field = "django.db.models.BigAutoField"
#     name = "apps.audit"


from django.apps import AppConfig


class AuditConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.audit"

    def ready(self):
        # Import signals here so they are registered when the app loads
        import apps.audit.signals  # noqa: F401
