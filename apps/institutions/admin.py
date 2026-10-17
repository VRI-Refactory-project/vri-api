from django.contrib import admin

from .models import InstitutionProfile, InstitutionStaff


@admin.register(InstitutionProfile)
class InstitutionProfileAdmin(admin.ModelAdmin):
    list_display = [
        "organisation_name",
        "organisation_type",
        "organisation_type_other",
        "status",
        "user",
        "created_at",
    ]
    list_filter = ["status", "organisation_type"]
    search_fields = [
        "organisation_name",
        "user__full_name",
        "user__email",
        "user__phone_number",
    ]
    list_select_related = ["user"]


@admin.register(InstitutionStaff)
class InstitutionStaffAdmin(admin.ModelAdmin):
    """V2-006. Staff are managed by each institution's administrators through the API; this page is for
    platform admins to look things up."""

    list_display = [
        "user",
        "institution",
        "role",
        "job_title",
        "revoked_at",
        "created_at",
    ]
    list_filter = ["role", ("revoked_at", admin.EmptyFieldListFilter)]
    search_fields = [
        "user__full_name",
        "user__email",
        "user__phone_number",
        "institution__organisation_name",
    ]
    list_select_related = ["user", "institution"]
    readonly_fields = [
        "created_by",
        "created_at",
        "updated_at",
        "revoked_at",
        "revoked_by",
    ]
