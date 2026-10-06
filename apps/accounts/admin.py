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


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    form = UserEditForm
    add_form = UserCreateForm
    ordering = ("-date_joined",)
    list_display = ("full_name", "phone_number", "email", "role", "is_active", "date_joined")
    list_filter = ("role", "is_active", "is_staff")
    search_fields = ("full_name", "phone_number", "email")
    fieldsets = (
        (None, {"fields": ("full_name", "phone_number", "email", "role", "password")}),
        ("Status", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("full_name", "phone_number", "email", "role", "password1", "password2"),
        }),
    )
    readonly_fields = ("last_login", "date_joined")
