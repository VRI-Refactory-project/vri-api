from django.contrib.auth import get_user_model
from rest_framework import serializers

from apps.accounts.serializers import BaseRegistrationSerializer

from .models import InstitutionProfile, InstitutionStaff, OrganisationType, StaffRole

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
        institution = InstitutionProfile.objects.create(
            user=user,
            organisation_name=validated_data["organisation_name"].strip(),
            organisation_type=validated_data["organisation_type"],
            organisation_type_other=validated_data["organisation_type_other"],
        )
        # The registrant is the institution's first administrator (V2-006).
        InstitutionStaff.objects.create(institution=institution, user=user, role=StaffRole.ADMIN, created_by=user)


class InstitutionSerializer(serializers.ModelSerializer):
    """An institution as its own users and platform admins see it."""

    class Meta:
        model = InstitutionProfile
        fields = ["id", "organisation_name", "organisation_type", "organisation_type_other", "status",
                  "created_at"]
        read_only_fields = fields


# --- V2-006: staff accounts -------------------------------------------------------------------------


class StaffSerializer(serializers.ModelSerializer):
    """One staff member as their institution's administrators see them."""

    user_id = serializers.IntegerField(source="user.id", read_only=True)
    full_name = serializers.CharField(source="user.full_name", read_only=True)
    phone_number = serializers.CharField(source="user.phone_number", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    status = serializers.SerializerMethodField(help_text="ACTIVE or REVOKED.")
    last_login = serializers.DateTimeField(source="user.last_login", read_only=True)

    class Meta:
        model = InstitutionStaff
        fields = ["id", "user_id", "full_name", "phone_number", "email", "role", "job_title", "status",
                  "last_login", "created_at", "revoked_at"]
        read_only_fields = fields

    def get_status(self, membership) -> str:
        return "REVOKED" if membership.is_revoked else "ACTIVE"


class StaffCreateSerializer(BaseRegistrationSerializer):
    """An institution administrator adds a staff member (V2-006).

    Same checks as registration for name, phone and email (email is required: the login details are sent
    there). No password: a temporary one is emailed. Needs `institution` and `request` in the context.
    `save()` returns the new InstitutionStaff.
    """

    role = User.Role.INSTITUTION  # the account's platform role; the institution role is the "role" field below
    email_required = True
    job_title = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")

    def get_fields(self):
        fields = super().get_fields()
        del fields["password"], fields["terms_accepted"]
        fields["role"] = serializers.ChoiceField(choices=StaffRole.choices, default=StaffRole.STAFF)
        return fields

    def validate(self, attrs):
        # Skip the parent's password rules: there is no password yet.
        return serializers.Serializer.validate(self, attrs)

    def create(self, validated_data):
        validated_data["password"] = None  # unusable until staff.create_staff sets a temporary one
        self.staff_role = validated_data.pop("role")
        user = super().create(validated_data)
        return user.institution_staff

    def create_profile(self, user, validated_data):
        InstitutionStaff.objects.create(
            institution=self.context["institution"],
            user=user,
            role=self.staff_role,
            job_title=validated_data.get("job_title", "").strip(),
            created_by=self.context["request"].user,
        )

    def to_representation(self, instance):
        return StaffSerializer(instance).data


class StaffUpdateSerializer(serializers.Serializer):
    """What an institution administrator may change. Phone and email are the person's sign-in details,
    so only the person themselves changes those."""

    full_name = serializers.CharField(max_length=150, required=False)
    job_title = serializers.CharField(max_length=100, required=False, allow_blank=True)
    role = serializers.ChoiceField(choices=StaffRole.choices, required=False)

    def validate_full_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Enter the person's full name.")
        return value
