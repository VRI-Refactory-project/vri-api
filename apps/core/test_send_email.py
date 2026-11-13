"""send_branded_email: Brevo's API when a key is set, Django's email backend otherwise."""

import json
import urllib.error
from unittest import mock

from django.core import mail
from django.test import SimpleTestCase, override_settings

from .emailing import send_branded_email

FROM = "SignConnect <no-reply@signconnect.example>"


def send(**overrides):
    args = {
        "subject": "Hello",
        "text": "Plain body",
        "html": "<p>Html body</p>",
        "to_email": "amina@example.com",
        "to_name": "Amina Okafor",
    }
    args.update(overrides)
    return send_branded_email(**args)


@override_settings(DEFAULT_FROM_EMAIL=FROM, BREVO_API_KEY="")
class WithoutBrevoTests(SimpleTestCase):
    def test_uses_django_mail_with_an_html_part(self):
        send()
        message = mail.outbox[-1]
        self.assertEqual(message.subject, "Hello")
        self.assertEqual(message.to, ["amina@example.com"])
        self.assertEqual(message.body, "Plain body")
        self.assertEqual(message.alternatives[0], ("<p>Html body</p>", "text/html"))


@override_settings(DEFAULT_FROM_EMAIL=FROM, BREVO_API_KEY="test-key")
class WithBrevoTests(SimpleTestCase):
    def test_sends_through_the_brevo_api_and_not_django_mail(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            send()
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.brevo.com/v3/smtp/email")
        self.assertEqual(request.get_header("Api-key"), "test-key")
        payload = json.loads(request.data)
        self.assertEqual(payload["sender"], {"name": "SignConnect", "email": "no-reply@signconnect.example"})
        self.assertEqual(payload["to"], [{"email": "amina@example.com", "name": "Amina Okafor"}])
        self.assertEqual(payload["subject"], "Hello")
        self.assertEqual(payload["htmlContent"], "<p>Html body</p>")
        self.assertEqual(payload["textContent"], "Plain body")
        self.assertEqual(len(mail.outbox), 0)

    def test_a_missing_name_falls_back_to_the_address(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            send(to_name="")
        payload = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(payload["to"][0]["name"], "amina@example.com")

    def test_a_rejection_is_logged_under_the_callers_logger_and_raised(self):
        error = urllib.error.HTTPError("url", 403, "Forbidden", {}, None)
        error.read = lambda: b'{"message":"unrecognised IP address"}'
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertLogs("apps.test.caller", "ERROR") as logs:
                with self.assertRaises(RuntimeError):
                    import logging

                    send(log=logging.getLogger("apps.test.caller"))
        self.assertIn("403", logs.output[0])
        self.assertIn("unrecognised IP", logs.output[0])
        self.assertNotIn("test-key", logs.output[0])
        self.assertNotIn("Plain body", logs.output[0])

    def test_logs_to_its_own_logger_when_the_caller_gives_none(self):
        error = urllib.error.URLError("no route")
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertLogs("apps.core.emailing", "ERROR"):
                with self.assertRaises(RuntimeError):
                    send()

    @override_settings(DEFAULT_FROM_EMAIL="")
    def test_refuses_to_send_without_a_valid_sender_address(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            with self.assertLogs("apps.core.emailing", "ERROR"):
                with self.assertRaises(RuntimeError):
                    send()
        urlopen.assert_not_called()
