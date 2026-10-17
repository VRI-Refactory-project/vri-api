from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm

from .models import User


class UserCreateForm(AdminUserCreationForm):
    class Meta:
        model = User
        fields = ("full_name", "phone_number", "email", "role")


class UserEditForm(UserChangeForm):
    email = forms.EmailField(required=False)

    class Meta:
        model = User
        fields = "__all__"


class EmailVerifiedFilter(admin.SimpleListFilter):
    title = "email verified"
    parameter_name = "email_verified"

    def lookups(self, request, model_admin):
        return [
            ("yes", "Verified"),
            ("no", "Has email, not verified"),
            ("none", "No email"),
        ]

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(email__isnull=False, email_verified_at__isnull=False)
        if self.value() == "no":
            return queryset.filter(email__isnull=False, email_verified_at__isnull=True)
        if self.value() == "none":
            return queryset.filter(email__isnull=True)
        return queryset


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    form = UserEditForm
    add_form = UserCreateForm
    ordering = ("-date_joined",)
    list_display = (
        "full_name",
        "phone_number",
        "email",
        "email_verified",
        "role",
        "is_active",
        "date_joined",
    )
    list_filter = ("role", "is_active", "is_staff", EmailVerifiedFilter)
    search_fields = ("full_name", "phone_number", "email")
    fieldsets = (
        (None, {"fields": ("full_name", "phone_number", "email", "role", "password")}),
        (
            "Status",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        (
            "Dates",
            {
                "fields": (
                    "last_login",
                    "date_joined",
                    "terms_accepted_at",
                    "email_verified_at",
                )
            },
        ),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "full_name",
                    "phone_number",
                    "email",
                    "role",
                    "password1",
                    "password2",
                ),
            },
        ),
    )
    readonly_fields = (
        "last_login",
        "date_joined",
        "terms_accepted_at",
        "email_verified_at",
    )

    @admin.display(boolean=True, description="Email verified")
    def email_verified(self, obj):
        return obj.email_verified

    def save_model(self, request, obj, form, change):
        # A changed address has not been proven yet.
        if change and "email" in form.changed_data:
            obj.email_verified_at = None
        super().save_model(request, obj, form, change)
