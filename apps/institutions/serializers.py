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

    def create_profile(self, user, validated_data):
        InstitutionProfile.objects.create(
            user=user,
            organisation_name=validated_data["organisation_name"].strip(),
            organisation_type=validated_data["organisation_type"],
        )
