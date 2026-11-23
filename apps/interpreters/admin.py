from django.contrib import admin, messages

from . import scheduling
from .models import (
    CertificationDocument,
    InterpreterProfile,
    ScheduledSession,
    ScheduledSessionOffer,
    VerificationDecision,
)


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
    fields = [
        "created_at",
        "action",
        "admin",
        "previous_status",
        "new_status",
        "reason",
    ]
    readonly_fields = fields


@admin.register(VerificationDecision)
class VerificationDecisionAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = [
        "created_at",
        "action",
        "interpreter",
        "admin",
        "previous_status",
        "new_status",
    ]
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


class ScheduledSessionOfferInline(ReadOnlyAdminMixin, admin.TabularInline):
    model = ScheduledSessionOffer
    extra = 0
    readonly_fields = ["interpreter", "outcome", "offered_at", "responded_at"]


class NeedsAttentionFilter(admin.SimpleListFilter):
    title = "needs attention"
    parameter_name = "attention"

    def lookups(self, request, model_admin):
        return [("yes", "Still without an interpreter")]

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(pk__in=scheduling.unconfirmed().values("pk"))
        return queryset


@admin.register(ScheduledSession)
class ScheduledSessionAdmin(admin.ModelAdmin):
    """Booked sessions. The only thing to change here is who interprets: pick an interpreter and save,
    and the booking is confirmed with them (the person who booked and the interpreter are told)."""

    list_display = [
        "__str__",
        "session_type",
        "status",
        "no_show",
        "interpreter",
        "institution",
    ]
    list_filter = [NeedsAttentionFilter, "status", "no_show", "session_type"]
    search_fields = ["deaf_user__full_name", "interpreter__user__full_name"]
    date_hierarchy = "date"
    inlines = [ScheduledSessionOfferInline]
    actions = ["offer_again"]
    autocomplete_fields = ["interpreter"]
    readonly_fields = [
        "deaf_user",
        "institution",
        "date",
        "time",
        "duration",
        "session_type",
        "custom_session_type",
        "status",
        "no_show",
        "confirmed_at",
        "offered_at",
        "reminders_sent",
        "created_at",
        "updated_at",
    ]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if "interpreter" not in form.changed_data:
            return
        profile = form.cleaned_data["interpreter"]
        if profile is None:
            messages.error(
                request,
                "To take an interpreter off, they release it, or pick someone else.",
            )
            return
        try:
            scheduling.assign_session(obj.pk, profile)
        except scheduling.BookingNotAvailable as error:
            messages.error(request, str(error))

    @admin.action(description="Offer again to interpreters who are free")
    def offer_again(self, request, queryset):
        offered = sum(
            len(scheduling.offer_session(session))
            for session in queryset.filter(status=ScheduledSession.Status.PENDING)
        )
        self.message_user(request, f"Offered to {offered} interpreter(s).")
