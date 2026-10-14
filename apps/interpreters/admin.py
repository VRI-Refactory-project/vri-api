from django.contrib import admin
from .models import InterpreterProfile, CertificationDocument, VerificationDecision


class CertificationDocumentInline(admin.TabularInline):
    model = CertificationDocument
    extra = 0
    readonly_fields = ["uploaded_at"]


class ReadOnlyAdminMixin:
    """The verification audit log can be read in the admin but never added to, edited or deleted."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class VerificationDecisionInline(ReadOnlyAdminMixin, admin.TabularInline):
    model = VerificationDecision
    extra = 0
    fields = ["created_at", "action", "admin", "previous_status", "new_status", "reason"]
    readonly_fields = fields


@admin.register(VerificationDecision)
class VerificationDecisionAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ["created_at", "action", "interpreter", "admin", "previous_status", "new_status"]
    list_filter = ["action", "new_status"]
    search_fields = ["interpreter__user__full_name", "admin__full_name", "reason"]
    list_select_related = ["interpreter__user", "admin"]


@admin.register(InterpreterProfile)
class InterpreterProfileAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "verification_status",
        "specialisation",
        "is_available",
        "created_at",
    ]
    list_filter = ["verification_status", "specialisation", "is_available"]
    search_fields = ["user__full_name", "user__email", "user__phone_number"]
    list_select_related = ["user"]
    inlines = [CertificationDocumentInline, VerificationDecisionInline]


@admin.register(CertificationDocument)
class CertificationDocumentAdmin(admin.ModelAdmin):
    list_display = [
        "interpreter",
        "document_name",
        "certificate_type",
        "issuing_organisation",
        "uploaded_at",
    ]
    search_fields = ["interpreter__user__full_name", "document_name"]
    list_select_related = ["interpreter__user"]
