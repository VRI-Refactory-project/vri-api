from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import serializers

from .models import UserProfile

User = get_user_model()


class UserRegistrationSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(required=True)
    password = serializers.CharField(
        write_only=True, required=True, style={"input_type": "password"}
    )
    contact_number = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )

    class Meta:
        model = User
        fields = ["email", "password", "contact_number"]

    def validate_email(self, value):
        """Ensure email is unique across all users."""
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError(
                "A user with this email address already exists."
            )
        return value

    def validate_contact_number(self, value):
        """If a contact number is provided, check that it isn't already registered."""
        if value and value.strip() and UserProfile.objects.filter(contact_number=value.strip()).exists():
           raise serializers.ValidationError(
                    "This contact number is already registered to another account."
                )
        return value

    @transaction.atomic
    def create(self, validated_data):
        email = validated_data["email"]
        password = validated_data["password"]
        contact_number = validated_data.get("contact_number") or None

        # Use email as username for web login
        user = User.objects.create_user(username=email, email=email, password=password)

        # Attach profile with optional phone number
        UserProfile.objects.create(
            user=user, contact_number=contact_number if contact_number else None
        )
        return user


class UserProfileSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(source="user.email", read_only=True)
    full_name = serializers.CharField(source="user.get_full_name", read_only=True)

    class Meta:
        model = UserProfile
        fields = (
            "id",
            "email",
            "full_name",
            "preferred_name",
            "contact_number",
            "location",
            "emergency_contact_name",
            "emergency_contact_phone",
            "sign_language_preference",
            "communication_methods",
            "flash_alerts",
            "vibrate_on_call",
            "live_captions",
            "large_text",
            "preferred_domains",
            "preferred_contact_mode",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")
