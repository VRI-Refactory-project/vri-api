from django.contrib import admin

from .models import AdminProfile


@admin.register(AdminProfile)
class AdminProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "session_alerts",
        "verification_alerts",
        "password_last_changed",
    )
    search_fields = ("user__email", "user__full_name")
