# Register your models here.
from django.contrib import admin
from .models import SessionRequest


@admin.register(SessionRequest)
class SessionRequestAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "requester",
        "specialisation",
        "is_emergency",
        "status",
        "offered_interpreter",
        "created_at",
        "queued_at",
    )
    list_filter = ("status", "is_emergency", "specialisation")
    search_fields = (
        "requester__email",
        "offered_interpreter__email",
        "request_key",
    )
    readonly_fields = (
        "created_at",
        "queued_at",
        "offered_at",
        "accepted_at",
        "failed_at",
    )
