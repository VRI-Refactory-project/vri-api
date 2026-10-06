from django.contrib import admin
from .models import InterpreterProfile, CertificationDocument


class CertificationDocumentInline(admin.TabularInline):
    model = CertificationDocument
    extra = 0
    readonly_fields = ["uploaded_at"]


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
    search_fields = ["user__username", "user__email"]
    list_select_related = ["user"]
    inlines = [CertificationDocumentInline]


@admin.register(CertificationDocument)
class CertificationDocumentAdmin(admin.ModelAdmin):
    list_display = ["interpreter", "document_name", "uploaded_at"]
    search_fields = ["interpreter__user__username", "document_name"]
    list_select_related = ["interpreter__user"]
