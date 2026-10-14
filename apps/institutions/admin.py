from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from rest_framework.exceptions import ValidationError

from apps.accounts.recovery import mask_email

from . import credentials
from .models import InstitutionProfile, OrganisationType


class InstitutionCreateForm(forms.Form):
    """V2-005: what a platform administrator fills in to create an approved institution."""

    organisation_name = forms.CharField(max_length=200)
    organisation_type = forms.ChoiceField(choices=OrganisationType.choices)
    organisation_type_other = forms.CharField(
        max_length=100, required=False, label="Type of institution (if Other)",
    )
    full_name = forms.CharField(max_length=150, label="Institution administrator's full name")
    phone_number = forms.CharField(label="Their phone number", help_text="e.g. 0772123456 or +256772123456")
    email = forms.EmailField(label="Their email", help_text="The login details are sent to this address.")


@admin.register(InstitutionProfile)
class InstitutionProfileAdmin(admin.ModelAdmin):
    list_display = ["organisation_name", "organisation_type", "organisation_type_other", "status", "user", "created_at"]
    list_filter = ["status", "organisation_type"]
    search_fields = ["organisation_name", "user__full_name", "user__email", "user__phone_number"]
    list_select_related = ["user"]

    def add_view(self, request, form_url="", extra_context=None):
        """"Add institution" creates the institution and its administrator, approved, and emails the
        login details: the same as POST /api/v1/manage/institutions/."""
        if not self.has_add_permission(request):
            raise PermissionDenied
        form = InstitutionCreateForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            try:
                user = credentials.create_approved_institution(form.cleaned_data, request.user, request)
            except ValidationError as exc:
                for field, errors in exc.detail.items():
                    form.add_error(field if field in form.fields else None, [str(e) for e in errors])
            except credentials.CredentialsEmailError:
                form.add_error(None, "The login details could not be emailed, so the institution was not "
                                     "created. Check the email settings and try again.")
            else:
                self.message_user(request, f"{user.institution_profile.organisation_name} was created and approved. "
                                           f"Login details were sent to {mask_email(user.email)}.", messages.SUCCESS)
                return redirect("admin:institutions_institutionprofile_change", user.institution_profile.pk)
        context = {
            **self.admin_site.each_context(request),
            "title": "Create an institution account",
            "opts": self.model._meta,
            "form": form,
            **(extra_context or {}),
        }
        return TemplateResponse(request, "admin/institutions/create_institution.html", context)
