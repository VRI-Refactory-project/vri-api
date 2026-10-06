from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from phonenumber_field.serializerfields import PhoneNumberField
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken

from .utils import find_user_by_identifier

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    """Public view of a user. Reuse this inside role-specific profile responses."""

    phone_number = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = ["id", "full_name", "phone_number", "email", "role", "date_joined"]
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
    phone_number = PhoneNumberField()
    email = serializers.EmailField(required=False, allow_blank=True, allow_null=True)
    password = serializers.CharField(write_only=True, style={"input_type": "password"})

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
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        candidate = User(full_name=attrs.get("full_name", ""), email=attrs.get("email"))
        try:
            validate_password(attrs["password"], user=candidate)
        except Exception as exc:  # django.core.exceptions.ValidationError
            raise serializers.ValidationError({"password": list(getattr(exc, "messages", [str(exc)]))})
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
        with transaction.atomic():
            user = User.objects.create_user(role=self.role, **user_data)
            self.create_profile(user, validated_data)
        return user

    def to_representation(self, instance):
        return UserSerializer(instance).data


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
