from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ImproperlyConfigured, ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from phonenumber_field.serializerfields import PhoneNumberField
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken

from . import recovery
from .models import RecoveryCode
from .utils import find_user_by_identifier

User = get_user_model()


class PlusPhoneNumberField(PhoneNumberField):
    default_error_messages = {
        "invalid": "Enter a valid phone number.",
    }


class UserSerializer(serializers.ModelSerializer):
    """Public view of a user. Reuse this inside role-specific profile responses."""

    phone_number = serializers.CharField(read_only=True)
    email_verified = serializers.BooleanField(read_only=True)

    class Meta:
        model = User
        fields = [
            "id", "full_name", "phone_number", "email", "email_verified", "role",
            "must_change_password", "date_joined",
        ]
        read_only_fields = fields


class BaseRegistrationSerializer(serializers.Serializer):
    """Shared registration logic. Every role's registration serializer subclasses this.

    Subclasses MUST set `role`, and SHOULD override `create_profile()` to create the
    role's profile. Set `email_required = True` to make email mandatory (interpreters).

    Everything in `create()` runs in one database transaction: if the profile fails,
    the user is rolled back too, so no half-created accounts.

        class DeafRegistrationSerializer(BaseRegistrationSerializer):
            role = User.Role.DEAF_HOH
            location = serializers.CharField()

            def create_profile(self, user, validated_data):
                DeafUserProfile.objects.create(user=user, location=validated_data["location"])
    """

    role = None
    email_required = False

    full_name = serializers.CharField(max_length=150)
    phone_number = PlusPhoneNumberField()
    email = serializers.EmailField(required=False, allow_blank=True, allow_null=True)
    password = serializers.CharField(write_only=True, style={"input_type": "password"})
    # Sent as true when the person ticks "I accept the Terms and Privacy Policy". The time is stored.
    terms_accepted = serializers.BooleanField(required=False, default=False, write_only=True)

    USER_FIELDS = ("full_name", "phone_number", "email", "password")

    def get_fields(self):
        fields = super().get_fields()
        if self.email_required:
            fields["email"] = serializers.EmailField(required=True)
        return fields

    def validate_phone_number(self, value):
        if User.objects.filter(phone_number=value).exists():
            raise serializers.ValidationError("An account with this phone number already exists.")
        return value

    def validate_email(self, value):
        if not value:
            return None

        value = value.strip().lower()

        local_part = value.split("@", 1)[0]

        if local_part.isdigit():
            raise serializers.ValidationError(
                "Email address cannot contain only numbers before the @ symbol."
            )

        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")

        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        candidate = User(full_name=attrs.get("full_name", ""), email=attrs.get("email"))
        check_new_password(attrs["password"], candidate, field="password")
        return attrs

    def create_profile(self, user, validated_data):
        """Override in subclasses to create the role's profile (and anything else)."""

    def create(self, validated_data):
        if self.role is None:
            raise NotImplementedError("Registration serializers must set `role`.")
        if self.role not in User.SELF_REGISTRATION_ROLES:
            # Public registration must never create admins (or any role users cannot pick themselves).
            raise ImproperlyConfigured(
                f"Registration serializers can only create these roles: {', '.join(User.SELF_REGISTRATION_ROLES)}."
            )
        user_data = {k: validated_data.pop(k) for k in self.USER_FIELDS if k in validated_data}
        if validated_data.pop("terms_accepted", False):
            user_data["terms_accepted_at"] = timezone.now()
        with transaction.atomic():
            user = User.objects.create_user(role=self.role, **user_data)
            self.create_profile(user, validated_data)
        return user

    def to_representation(self, instance):
        return UserSerializer(instance).data


class DeafRegistrationSerializer(BaseRegistrationSerializer):
    """V2-001 first step: name, phone number and a password/PIN. Email is optional.

    Communication method, location and emergency contact belong to the profile that is completed
    after sign-up (a DeafUserProfile does not exist yet), so they are not collected here.
    """

    role = User.Role.DEAF_HOH


class LoginSerializer(serializers.Serializer):
    identifier = serializers.CharField(
        help_text="Phone number (e.g. 0772123456 or +256772123456) or email address."
    )
    password = serializers.CharField(write_only=True, style={"input_type": "password"})

    default_error_messages = {
        "invalid_credentials": "Incorrect phone number/email or password.",
        "inactive": "This account has been deactivated. Please contact support.",
    }

    def validate(self, attrs):
        user = find_user_by_identifier(attrs["identifier"])
        if user is None:
            User().set_password(attrs["password"])  # equalise timing for unknown accounts
            self.fail("invalid_credentials")
        if not user.check_password(attrs["password"]):
            self.fail("invalid_credentials")
        if not user.is_active:
            self.fail("inactive")
        attrs["user"] = user
        return attrs


class TokenPairSerializer(serializers.Serializer):
    """Response shape for a successful login."""

    access = serializers.CharField()
    refresh = serializers.CharField()
    user = UserSerializer()

    @staticmethod
    def for_user(user):
        refresh = RefreshToken.for_user(user)
        refresh["role"] = user.role
        return {"access": str(refresh.access_token), "refresh": str(refresh), "user": UserSerializer(user).data}


def check_new_password(password, user, field="new_password"):
    try:
        validate_password(password, user=user)
    except DjangoValidationError as exc:
        raise serializers.ValidationError({field: list(exc.messages)})


CodeField = serializers.RegexField(
    r"^\d{6}$", max_length=6, min_length=6, error_messages={"invalid": "Enter the 6-digit code from the email."}
)


class RecoveryRequestSerializer(serializers.Serializer):
    identifier = serializers.CharField(help_text="Phone number or email the account was registered with.")


class RecoveryConfirmSerializer(serializers.Serializer):
    identifier = serializers.CharField()
    code = CodeField
    new_password = serializers.CharField(write_only=True, style={"input_type": "password"})

    default_error_messages = {"invalid_code": "Invalid or expired code. Request a new one."}

    def validate(self, attrs):
        user = find_user_by_identifier(attrs["identifier"])
        if user is not None and not user.is_active:
            user = None  # recovery never reactivates a deactivated account
        rc = recovery.check_code(user, attrs["code"], RecoveryCode.Purpose.SELF_RECOVERY)
        if rc is None:
            raise serializers.ValidationError({"code": [self.error_messages["invalid_code"]]})
        # Only now (code proven) do we reveal password-rule errors.
        check_new_password(attrs["new_password"], user)
        attrs["user"], attrs["recovery_code"] = user, rc
        return attrs


class EmailVerifyConfirmSerializer(serializers.Serializer):
    code = CodeField


class AdminResetConfirmSerializer(serializers.Serializer):
    code = CodeField
    temporary_password = serializers.CharField(
        write_only=True, style={"input_type": "password"},
        help_text="Temporary password/PIN to give the user in person. They must change it at next sign-in.",
    )


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, style={"input_type": "password"})
    new_password = serializers.CharField(write_only=True, style={"input_type": "password"})

    def validate(self, attrs):
        user = self.context["request"].user
        if not user.check_password(attrs["current_password"]):
            raise serializers.ValidationError({"current_password": ["Incorrect password."]})
        if attrs["current_password"] == attrs["new_password"]:
            raise serializers.ValidationError({"new_password": ["Choose a different password from your current one."]})
        check_new_password(attrs["new_password"], user)
        return attrs
