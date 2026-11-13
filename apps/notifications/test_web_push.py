"""Browser notifications: subscribing, and sending to the subscribed browsers."""

import json
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from py_vapid import Vapid
from pywebpush import WebPushException
from rest_framework import status
from rest_framework.test import APITestCase

from apps.notifications.models import Notification, WebPushSubscription
from apps.notifications.services import create_notification
from apps.notifications.web_push import send_web_push

User = get_user_model()

ENDPOINT = "https://push.example.com/send/abc123"
SUBSCRIPTION = {"endpoint": ENDPOINT, "keys": {"p256dh": "key-one", "auth": "key-two"}}
ON = {
    "WEB_PUSH_ENABLED": True,
    "WEB_PUSH_VAPID_PUBLIC_KEY": "public-key",
    "WEB_PUSH_VAPID_PRIVATE_KEY": "private-key",
    "WEB_PUSH_CONTACT": "mailto:team@example.com",
}


class WebPushSubscribeTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            phone_number="+256700000201", password="TestPassword123!", role="deaf_hoh"
        )
        self.other = User.objects.create_user(
            phone_number="+256700000202", password="TestPassword123!", role="deaf_hoh"
        )
        self.url = reverse("notifications:web-push")
        self.client.force_authenticate(self.user)

    def test_subscribing_needs_a_signed_in_user(self):
        self.client.force_authenticate(None)

        response = self.client.post(self.url, SUBSCRIPTION, format="json")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_subscribing_saves_the_browser_for_the_user(self):
        response = self.client.post(self.url, SUBSCRIPTION, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        saved = WebPushSubscription.objects.get()
        self.assertEqual(saved.user, self.user)
        self.assertEqual(saved.endpoint, ENDPOINT)
        self.assertEqual((saved.p256dh, saved.auth), ("key-one", "key-two"))
        self.assertTrue(saved.is_active)

    def test_subscribing_twice_keeps_one_row(self):
        self.client.post(self.url, SUBSCRIPTION, format="json")
        self.client.post(self.url, SUBSCRIPTION, format="json")

        self.assertEqual(WebPushSubscription.objects.count(), 1)

    def test_a_browser_moves_to_the_person_who_signed_in_last(self):
        self.client.post(self.url, SUBSCRIPTION, format="json")
        self.client.force_authenticate(self.other)

        self.client.post(self.url, SUBSCRIPTION, format="json")

        self.assertEqual(WebPushSubscription.objects.get().user, self.other)

    def test_a_subscription_without_keys_is_refused(self):
        response = self.client.post(self.url, {"endpoint": ENDPOINT}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(WebPushSubscription.objects.exists())

    def test_turning_off_switches_the_browser_off(self):
        self.client.post(self.url, SUBSCRIPTION, format="json")

        response = self.client.delete(self.url, {"endpoint": ENDPOINT}, format="json")

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(WebPushSubscription.objects.get().is_active)

    def test_nobody_can_turn_off_someone_elses_browser(self):
        self.client.post(self.url, SUBSCRIPTION, format="json")
        self.client.force_authenticate(self.other)

        self.client.delete(self.url, {"endpoint": ENDPOINT}, format="json")

        self.assertTrue(WebPushSubscription.objects.get().is_active)

    def test_turning_off_needs_the_address(self):
        response = self.client.delete(self.url, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class WebPushKeyTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            phone_number="+256700000203", password="TestPassword123!", role="deaf_hoh"
        )
        self.url = reverse("notifications:web-push-key")
        self.client.force_authenticate(self.user)

    def test_the_key_needs_a_signed_in_user(self):
        self.client.force_authenticate(None)

        self.assertEqual(
            self.client.get(self.url).status_code, status.HTTP_401_UNAUTHORIZED
        )

    def test_the_key_is_empty_while_web_push_is_off(self):
        response = self.client.get(self.url)

        self.assertEqual(response.data, {"public_key": ""})

    @override_settings(**ON)
    def test_the_key_is_given_once_web_push_is_on(self):
        response = self.client.get(self.url)

        self.assertEqual(response.data, {"public_key": "public-key"})

    @override_settings(**{**ON, "WEB_PUSH_VAPID_PRIVATE_KEY": ""})
    def test_the_key_stays_empty_when_a_setting_is_missing(self):
        self.assertEqual(self.client.get(self.url).data, {"public_key": ""})


@override_settings(**ON)
class SendWebPushTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            phone_number="+256700000204", password="TestPassword123!", role="deaf_hoh"
        )
        self.other = User.objects.create_user(
            phone_number="+256700000205", password="TestPassword123!", role="deaf_hoh"
        )
        self.notification = Notification.objects.create(
            user=self.user,
            title="Interpreter found",
            body="An interpreter has accepted your request.",
            data={"request_id": 7},
        )
        self.subscription = WebPushSubscription.objects.create(
            user=self.user, endpoint=ENDPOINT, p256dh="key-one", auth="key-two"
        )

    @mock.patch("apps.notifications.web_push.webpush")
    def test_sends_to_the_persons_browser(self, webpush):
        send_web_push(self.notification)

        webpush.assert_called_once()
        kwargs = webpush.call_args.kwargs
        self.assertEqual(kwargs["subscription_info"]["endpoint"], ENDPOINT)
        self.assertEqual(
            kwargs["subscription_info"]["keys"],
            {"p256dh": "key-one", "auth": "key-two"},
        )
        self.assertEqual(kwargs["vapid_claims"], {"sub": "mailto:team@example.com"})
        message = json.loads(kwargs["data"])
        self.assertEqual(message["title"], "Interpreter found")
        self.assertEqual(message["data"], {"request_id": 7})

    @mock.patch("apps.notifications.web_push.webpush")
    def test_leaves_out_other_people_and_switched_off_browsers(self, webpush):
        WebPushSubscription.objects.create(
            user=self.other,
            endpoint="https://push.example.com/send/other",
            p256dh="a",
            auth="b",
        )
        WebPushSubscription.objects.create(
            user=self.user,
            endpoint="https://push.example.com/send/off",
            p256dh="a",
            auth="b",
            is_active=False,
        )

        send_web_push(self.notification)

        self.assertEqual(webpush.call_count, 1)

    @mock.patch("apps.notifications.web_push.webpush")
    @override_settings(WEB_PUSH_ENABLED=False)
    def test_sends_nothing_while_web_push_is_off(self, webpush):
        send_web_push(self.notification)

        webpush.assert_not_called()

    @mock.patch("apps.notifications.web_push.webpush")
    def test_a_browser_that_unsubscribed_is_switched_off(self, webpush):
        webpush.side_effect = WebPushException(
            "gone", response=mock.Mock(status_code=410)
        )

        send_web_push(self.notification)

        self.subscription.refresh_from_db()
        self.assertFalse(self.subscription.is_active)

    @mock.patch("apps.notifications.web_push.webpush")
    def test_any_other_failure_keeps_the_browser_and_does_not_crash(self, webpush):
        webpush.side_effect = WebPushException(
            "busy", response=mock.Mock(status_code=500)
        )

        with self.assertLogs("apps.notifications.web_push", level="ERROR"):
            send_web_push(self.notification)

        self.subscription.refresh_from_db()
        self.assertTrue(self.subscription.is_active)

    @mock.patch("apps.notifications.services.send_expo_push")
    @mock.patch("apps.notifications.services.send_web_push")
    def test_every_new_notification_goes_to_browsers_too(self, send_web, send_expo):
        with self.captureOnCommitCallbacks(execute=True):
            saved = create_notification(user=self.user, title="Hello", body="World")

        send_web.assert_called_once_with(saved)
        send_expo.assert_called_once_with(saved)


class MakeVapidKeysTests(APITestCase):
    def test_the_keys_it_prints_can_sign_a_push(self):
        out = StringIO()
        call_command("make_vapid_keys", stdout=out)
        values = dict(
            line.split("=", 1) for line in out.getvalue().strip().splitlines()
        )

        signer = Vapid.from_string(values["WEB_PUSH_VAPID_PRIVATE_KEY"])
        headers = signer.sign(
            {"sub": "mailto:team@example.com", "aud": "https://push.example.com"}
        )

        self.assertIn("Authorization", headers)
