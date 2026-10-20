from django.contrib import admin

from .models import ServiceRequest


@admin.register(ServiceRequest)
class ServiceRequestAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "requester",
        "interpreter",
        "priority",
        "status",
        "created_at",
    )
    list_filter = ("status", "priority")
    search_fields = (
        "requester__email",
        "interpreter__user__email",
    )
    readonly_fields = ("created_at", "last_seen_at")
