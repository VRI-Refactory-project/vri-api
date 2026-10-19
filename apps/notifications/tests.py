from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.notifications.models import Notification, PushDevice

User = get_user_model()


class NotificationAPITests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            phone_number="+256700000101",
            password="TestPassword123!",
            role="deaf_hoh",
        )
        cls.other_user = User.objects.create_user(
            phone_number="+256700000102",
            password="TestPassword123!",
            role="deaf_hoh",
        )

        cls.notification = Notification.objects.create(
            user=cls.user,
            title="New interpreter request",
            body="You have received a new request.",
            category=Notification.Category.INCOMING_REQUEST,
            data={"channel": "video", "urgency": "high"},
        )
        cls.other_notification = Notification.objects.create(
            user=cls.other_user,
            title="Private notification",
            body="This belongs to another user.",
        )

    def test_notification_list_requires_authentication(self):
        response = self.client.get(reverse("notifications:list"))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_user_only_sees_their_own_notifications(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.get(reverse("notifications:list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["id"], self.notification.id)

    def test_user_can_mark_own_notification_as_read(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.post(
            reverse(
                "notifications:mark-read",
                kwargs={"pk": self.notification.pk},
            )
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.notification.refresh_from_db()
        self.assertIsNotNone(self.notification.read_at)

    def test_user_cannot_mark_another_users_notification_as_read(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.post(
            reverse(
                "notifications:mark-read",
                kwargs={"pk": self.other_notification.pk},
            )
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.other_notification.refresh_from_db()
        self.assertIsNone(self.other_notification.read_at)

    def test_marking_notification_read_twice_preserves_timestamp(self):
        self.client.force_authenticate(user=self.user)
        url = reverse(
            "notifications:mark-read",
            kwargs={"pk": self.notification.pk},
        )

        first_response = self.client.post(url)
        self.notification.refresh_from_db()
        original_read_at = self.notification.read_at

        second_response = self.client.post(url)
        self.notification.refresh_from_db()

        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.notification.read_at, original_read_at)

    def test_user_can_register_push_device(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.post(
            reverse("notifications:devices"),
            {
                "token": "ExponentPushToken[test-device-101]",
                "platform": "android",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(
            PushDevice.objects.filter(
                user=self.user,
                token="ExponentPushToken[test-device-101]",
                platform="android",
                is_active=True,
            ).exists()
        )

    def test_blank_push_token_is_rejected(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.post(
            reverse("notifications:devices"),
            {"token": "   ", "platform": "android"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_push_token_cannot_be_registered_to_another_user(self):
        PushDevice.objects.create(
            user=self.other_user,
            token="ExponentPushToken[shared-device]",
            platform="ios",
        )

        self.client.force_authenticate(user=self.user)

        response = self.client.post(
            reverse("notifications:devices"),
            {
                "token": "ExponentPushToken[shared-device]",
                "platform": "android",
            },
            format="json",
        )

        print("Response status:", response.status_code)
        print("Response data:", response.data)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_user_can_deactivate_their_push_device(self):
        device = PushDevice.objects.create(
            user=self.user,
            token="ExponentPushToken[device-to-deactivate]",
            platform="ios",
        )
        self.client.force_authenticate(user=self.user)

        response = self.client.delete(
            reverse("notifications:devices"),
            {"token": device.token},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_204_NO_CONTENT,
        )
        device.refresh_from_db()
        self.assertFalse(device.is_active)

    def test_user_cannot_deactivate_another_users_device(self):
        device = PushDevice.objects.create(
            user=self.other_user,
            token="ExponentPushToken[other-users-device]",
            platform="ios",
        )
        self.client.force_authenticate(user=self.user)

        response = self.client.delete(
            reverse("notifications:devices"),
            {"token": device.token},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_204_NO_CONTENT,
        )
        device.refresh_from_db()
        self.assertTrue(device.is_active)

