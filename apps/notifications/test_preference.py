"""Notification preferences: what each person chose to be pushed about."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.notifications.models import Notification, NotificationPreference
from apps.notifications.services import create_notification

User = get_user_model()


class PreferenceEndpointTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            phone_number="+256700000301",
            password="TestPassword123!",
            role="interpreter",
        )
        self.url = reverse("notifications:preferences")
        self.client.force_authenticate(self.user)

    def test_needs_a_signed_in_user(self):
        self.client.force_authenticate(None)

        self.assertEqual(
            self.client.get(self.url).status_code, status.HTTP_401_UNAUTHORIZED
        )

    def test_a_new_person_starts_with_the_defaults(self):
        response = self.client.get(self.url)

        self.assertEqual(
            response.data,
            {
                "session_requests": True,
                "session_reminders": True,
                "product_updates": False,
            },
        )

    def test_changing_one_switch_leaves_the_others(self):
        response = self.client.patch(
            self.url, {"session_requests": False}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data,
            {
                "session_requests": False,
                "session_reminders": True,
                "product_updates": False,
            },
        )
        self.assertFalse(
            NotificationPreference.objects.get(user=self.user).session_requests
        )

    def test_the_choice_is_still_there_next_time(self):
        self.client.patch(self.url, {"product_updates": True}, format="json")

        self.assertTrue(self.client.get(self.url).data["product_updates"])

    def test_a_switch_must_be_on_or_off(self):
        response = self.client.patch(
            self.url, {"session_requests": "maybe"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_each_person_has_their_own(self):
        other = User.objects.create_user(
            phone_number="+256700000302",
            password="TestPassword123!",
            role="interpreter",
        )
        self.client.patch(self.url, {"session_requests": False}, format="json")

        self.client.force_authenticate(other)

        self.assertTrue(self.client.get(self.url).data["session_requests"])


@mock.patch("apps.notifications.services.send_web_push")
@mock.patch("apps.notifications.services.send_expo_push")
class PreferencesDecideThePushTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            phone_number="+256700000303",
            password="TestPassword123!",
            role="interpreter",
        )

    def notify(self, category):
        with self.captureOnCommitCallbacks(execute=True):
            return create_notification(
                user=self.user, title="Hello", body="World", category=category
            )

    def test_a_new_request_is_pushed_by_default(self, expo, web):
        saved = self.notify(Notification.Category.INCOMING_REQUEST)

        expo.assert_called_once_with(saved)
        web.assert_called_once_with(saved)

    def test_switching_session_requests_off_stops_the_push_but_keeps_the_notification(
        self, expo, web
    ):
        NotificationPreference.objects.create(user=self.user, session_requests=False)

        self.notify(Notification.Category.INCOMING_REQUEST)

        expo.assert_not_called()
        web.assert_not_called()
        self.assertEqual(Notification.objects.filter(user=self.user).count(), 1)

    def test_the_offer_reminder_follows_the_same_switch(self, expo, web):
        NotificationPreference.objects.create(user=self.user, session_requests=False)

        self.notify(Notification.Category.OFFER_REMINDER)

        expo.assert_not_called()
        web.assert_not_called()

    def test_updates_about_your_own_session_are_always_pushed(self, expo, web):
        NotificationPreference.objects.create(
            user=self.user,
            session_requests=False,
            session_reminders=False,
            product_updates=False,
        )

        saved = self.notify(Notification.Category.GENERAL)

        expo.assert_called_once_with(saved)
        web.assert_called_once_with(saved)

    def test_one_person_switching_off_does_not_silence_another(self, expo, web):
        NotificationPreference.objects.create(user=self.user, session_requests=False)
        other = User.objects.create_user(
            phone_number="+256700000304",
            password="TestPassword123!",
            role="interpreter",
        )

        with self.captureOnCommitCallbacks(execute=True):
            saved = create_notification(
                user=other,
                title="Hello",
                body="World",
                category=Notification.Category.INCOMING_REQUEST,
            )

        expo.assert_called_once_with(saved)
