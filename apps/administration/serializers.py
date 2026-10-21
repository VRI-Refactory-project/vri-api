from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import AdminProfile

User = get_user_model()


class AdminProfileSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source="user.full_name", required=False)
    email = serializers.EmailField(source="user.email", required=False)
    phone_number = serializers.CharField(
        source="user.phone_number", required=False, allow_blank=True, default=""
    )
    contact_number = serializers.CharField(
        source="user.phone_number", required=False, allow_blank=True, default=""
    )
    profile_photo = serializers.ImageField(
        source="user.profile_photo", required=False, allow_null=True
    )

    class Meta:
        model = AdminProfile
        fields = (
            "id",
            "full_name",
            "email",
            "phone_number",
            "contact_number",
            "profile_photo",
            "session_alerts",
            "verification_alerts",
            "password_last_changed",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "password_last_changed",
            "created_at",
            "updated_at",
        )

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        # Provide both flat and nested 'user' keys to support any frontend state binding
        user = instance.user
        ret["user"] = {
            "id": user.id,
            "full_name": getattr(
                user,
                "full_name",
                f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip(),
            ),
            "email": user.email,
            "phone_number": getattr(user, "phone_number", ""),
            "contact_number": getattr(user, "phone_number", ""),
        }
        return ret

    def validate_email(self, value):
        request = self.context.get("request")
        if request and hasattr(request, "user"):
            user = request.user
            if User.objects.exclude(pk=user.pk).filter(email__iexact=value).exists():
                raise serializers.ValidationError(
                    "An account with this email already exists."
                )
        return value.lower()

    def update(self, instance, validated_data):
        user_data = validated_data.pop("user", {})
        user = instance.user

        # Support contact_number alias if passed by frontend
        if "contact_number" in user_data and "phone_number" not in user_data:
            user_data["phone_number"] = user_data.pop("contact_number")

        # Safely set full_name
        if "full_name" in user_data:
            full_name_val = user_data.pop("full_name", "").strip()
            if hasattr(user, "full_name") and not isinstance(
                type(user).full_name, property
            ):
                user.full_name = full_name_val
            else:
                parts = full_name_val.split(" ", 1)
                user.first_name = parts[0]
                user.last_name = parts[1] if len(parts) > 1 else ""

        # Update remaining user fields
        for attr, value in user_data.items():
            setattr(user, attr, value)
        user.save()

        # Update AdminProfile fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        return instance
