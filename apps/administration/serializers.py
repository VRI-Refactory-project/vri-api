from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone
from rest_framework import serializers

from .models import AdminProfile

User = get_user_model()


class AdminProfileSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source="user.full_name", required=False)
    email = serializers.EmailField(source="user.email", required=False)
    phone_number = serializers.CharField(
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

    def validate_email(self, value):
        user = self.context["request"].user
        if User.objects.exclude(pk=user.pk).filter(email__iexact=value).exists():
            raise serializers.ValidationError(
                "An account with this email already exists."
            )
        return value.lower()

    def update(self, instance, validated_data):
        user_data = validated_data.pop("user", {})
        user = instance.user
        for attr, value in user_data.items():
            setattr(user, attr, value)
        if user_data:
            user.save()

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance


class AdminChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(required=True, write_only=True)
    new_password = serializers.CharField(required=True, write_only=True)
    confirm_password = serializers.CharField(required=True, write_only=True)

    def validate_old_password(self, value):
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError(
                "Your current password was entered incorrectly."
            )
        return value

    def validate(self, attrs):
        if attrs["new_password"] != attrs["confirm_password"]:
            raise serializers.ValidationError(
                {"confirm_password": "New password and confirmation do not match."}
            )
        validate_password(attrs["new_password"], user=self.context["request"].user)
        return attrs

    def save(self, **kwargs):
        user = self.context["request"].user
        new_password = self.validated_data["new_password"]
        user.set_password(new_password)
        user.save()

        profile, _ = AdminProfile.objects.get_or_create(user=user)
        profile.password_last_changed = timezone.now()
        profile.save()
        return profile
