from django.contrib import admin

from .models import InstitutionProfile


@admin.register(InstitutionProfile)
class InstitutionProfileAdmin(admin.ModelAdmin):
    list_display = ["organisation_name", "organisation_type", "status", "user", "created_at"]
    list_filter = ["status", "organisation_type"]
    search_fields = ["organisation_name", "user__full_name", "user__email", "user__phone_number"]
    list_select_related = ["user"]
