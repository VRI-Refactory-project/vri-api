from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import serializers
from .models import InstitutionProfile

from apps.accounts.serializers import BaseRegistrationSerializer

from . import credentials
from .models import InstitutionProfile, InstitutionStatus, OrganisationType

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
        return self.clean_organisation_type(attrs)

    def clean_organisation_type(self, attrs):
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

    def create_profile(self, user, validated_data, status=InstitutionStatus.PENDING):
        InstitutionProfile.objects.create(
            user=user,
            organisation_name=validated_data["organisation_name"].strip(),
            organisation_type=validated_data["organisation_type"],
            organisation_type_other=validated_data["organisation_type_other"],
            status=status,
        )


class AdminInstitutionCreateSerializer(InstitutionRegistrationSerializer):
    """V2-005: a platform administrator creates an institution that is APPROVED straight away.

    Same fields and checks as self-registration, minus the password (a temporary one is emailed to the
    institution, see credentials.py) and the Terms box (only the institution can accept them).
    After save(), `temporary_password` holds the password to email.
    """

    def get_fields(self):
        fields = super().get_fields()
        del fields["password"], fields["terms_accepted"]
        return fields

    def validate(self, attrs):
        # Skip the password rules: there is no password yet.
        return self.clean_organisation_type(serializers.Serializer.validate(self, attrs))

    def create_profile(self, user, validated_data, status=InstitutionStatus.APPROVED):
        super().create_profile(user, validated_data, status=status)

    def create(self, validated_data):
        validated_data["password"] = None  # unusable until the temporary password is set below
        with transaction.atomic():
            user = super().create(validated_data)
            self.temporary_password = credentials.generate_temporary_password(user)
            user.set_password(self.temporary_password)
            user.must_change_password = True
            user.save(update_fields=["password", "must_change_password"])
        return user


class InstitutionSerializer(serializers.ModelSerializer):
    """An institution as its own users and platform admins see it."""

    class Meta:
        model = InstitutionProfile
        fields = ["id", "organisation_name", "organisation_type", "organisation_type_other", "status",
                  "created_at"]
        read_only_fields = fields



class InstitutionProfileUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = InstitutionProfile
        fields = ['name', 'contact_email', 'contact_phone', 'billing_address', 'timezone']
        extra_kwargs = {
            'name': {'required': False},
            'contact_email': {'required': False},
        }