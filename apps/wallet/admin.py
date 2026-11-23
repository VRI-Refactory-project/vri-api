from django.contrib import admin, messages

from . import services
from .models import WalletEntry


@admin.register(WalletEntry)
class WalletEntryAdmin(admin.ModelAdmin):
    """The ledger. Read-only: entries are settled with the actions below, never edited or deleted."""

    list_display = (
        "created_at",
        "user",
        "kind",
        "amount",
        "status",
        "network",
        "reference",
    )
    list_filter = ("status", "kind")
    search_fields = (
        "reference",
        "provider_ref",
        "user__full_name",
        "user__phone_number",
    )
    actions = ["mark_payouts_paid", "reject_payouts"]

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Mark selected payouts as paid (you have sent the money)")
    def mark_payouts_paid(self, request, queryset):
        done = sum(
            1
            for entry in queryset.filter(kind=WalletEntry.Kind.PAYOUT)
            if services.complete_payout(entry.pk)
        )
        self.message_user(
            request, f"{done} payout(s) marked as paid.", messages.SUCCESS
        )

    @admin.action(description="Reject selected payouts (money goes back to the person)")
    def reject_payouts(self, request, queryset):
        done = sum(
            1
            for entry in queryset.filter(kind=WalletEntry.Kind.PAYOUT)
            if services.reject_payout(entry.pk)
        )
        self.message_user(request, f"{done} payout(s) rejected.", messages.SUCCESS)
