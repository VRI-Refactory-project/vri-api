from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.notifications.models import Notification, PushDevice
from apps.notifications.serializers import (
    NotificationSerializer,
    PushDeviceRegistrationSerializer,
)
from apps.notifications.services import mark_notification_read


class NotificationListView(generics.ListAPIView):
    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user)

@extend_schema(
    request=None,
    responses={200: NotificationSerializer},
)


class NotificationMarkReadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        notification = get_object_or_404(
            Notification,
            pk=pk,
            user=request.user,
        )

        mark_notification_read(notification)

        return Response(
            NotificationSerializer(notification).data,
            status=status.HTTP_200_OK,
        )

@extend_schema(
    request=PushDeviceRegistrationSerializer,
    responses={200: None, 400: None, 409: None},
)


class PushDeviceRegistrationView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = PushDeviceRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        token = serializer.validated_data["token"]
        platform = serializer.validated_data["platform"]

        device = PushDevice.objects.filter(token=token).first()

        if device and device.user_id != request.user.pk:
            return Response(
                {"detail": "This push token is already registered."},
                status=status.HTTP_409_CONFLICT,
            )

        if device is None:
            device = PushDevice.objects.create(
                user=request.user,
                token=token,
                platform=platform,
                is_active=True,
            )
        else:
            device.platform = platform
            device.is_active = True
            device.save(
                update_fields=["platform", "is_active", "updated_at"]
            )

        return Response(
            {
                "detail": "Push device registered.",
                "platform": device.platform,
                "is_active": device.is_active,
            },
            status=status.HTTP_200_OK,
        )

    def delete(self, request):
        token = request.data.get("token")

        if not isinstance(token, str) or not token.strip():
            return Response(
                {"token": "A push token is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        device = PushDevice.objects.filter(
            user=request.user,
            token=token.strip(),
            is_active=True,
        ).first()

        if device:
            device.is_active = False
            device.save(update_fields=["is_active", "updated_at"])

        return Response(status=status.HTTP_204_NO_CONTENT)
