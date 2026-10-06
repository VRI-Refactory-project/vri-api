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
        """
        Ensure email is unique across all users.
        """
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError(
                "A user with this email address already exists."
            )
        return value

    def validate_contact_number(self, value):
        """
        If a contact number is provided, check that it isn't already registered.
        """
        if value and value.strip():
            if UserProfile.objects.filter(contact_number=value.strip()).exists():
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
