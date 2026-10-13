from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Read-only: an audit log that can be edited or deleted from the admin site is not a log."""

    list_display = ("created_at", "event", "user", "ip_address")
    list_filter = ("event",)
    search_fields = ("user__full_name", "user__phone_number", "ip_address")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
