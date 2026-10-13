from django.contrib.auth import get_user_model
from rest_framework import serializers

from apps.accounts.serializers import BaseRegistrationSerializer

from .models import InstitutionProfile, OrganisationType

User = get_user_model()


class InstitutionRegistrationSerializer(BaseRegistrationSerializer):
    """V2-005: the person registering is the institution's administrator (role `institution`).

    full_name, phone_number, email and password describe that administrator. An email is required.
    The institution starts PENDING until a platform admin approves it.
    """

    role = User.Role.INSTITUTION
    email_required = True
    organisation_name = serializers.CharField(max_length=200)
    organisation_type = serializers.ChoiceField(choices=OrganisationType.choices)
    organisation_type_other = serializers.CharField(max_length=100, required=False, allow_blank=True)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        other = (attrs.get("organisation_type_other") or "").strip()
        if attrs["organisation_type"] == OrganisationType.OTHER:
            if not other:
                raise serializers.ValidationError(
                    {"organisation_type_other": "Tell us what kind of institution this is."}
                )
            attrs["organisation_type_other"] = other
        else:
            attrs["organisation_type_other"] = ""  # only kept for "Other"
        return attrs

    def create_profile(self, user, validated_data):
        InstitutionProfile.objects.create(
            user=user,
            organisation_name=validated_data["organisation_name"].strip(),
            organisation_type=validated_data["organisation_type"],
            organisation_type_other=validated_data["organisation_type_other"],
        )
