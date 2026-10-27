"""The one-time-code emails: wording per purpose, and the Brevo API request."""

import json
import urllib.error
from types import SimpleNamespace
from unittest import mock

from django.core import mail
from django.test import SimpleTestCase, override_settings

from . import recovery
from .models import RecoveryCode

USER = SimpleNamespace(full_name="Amina Okafor", email="amina@example.com")
TARGET = SimpleNamespace(full_name="Phone Only", phone_number="0772000111")
FROM = "SignConnect <no-reply@signconnect.example>"


class CodeEmailPartsTests(SimpleTestCase):
    def parts(self, purpose, target=None):
        return recovery._code_email_parts(USER, "482910", purpose, target, 15)

    def test_verification_email(self):
        subject, text, html = self.parts(RecoveryCode.Purpose.EMAIL_VERIFY)
        self.assertEqual(subject, "Verify your SignConnect email")
        self.assertIn("482910", text)
        self.assertIn("expires in 15 minutes", text)
        self.assertIn(">482910</div>", html)
        self.assertIn("Verify your email address", html)

    def test_recovery_email_warns_not_to_share_the_code(self):
        subject, text, html = self.parts(RecoveryCode.Purpose.SELF_RECOVERY)
        self.assertEqual(subject, "Your SignConnect recovery code")
        self.assertIn("Never share this code", html)

    def test_admin_reset_names_the_person_being_reset(self):
        subject, text, html = self.parts(RecoveryCode.Purpose.ADMIN_RESET, TARGET)
        self.assertIn("Phone Only", text)
        self.assertIn("0772000111", html)

    def test_the_code_is_the_only_six_digit_number_in_the_text(self):
        import re

        _, text, _ = self.parts(RecoveryCode.Purpose.SELF_RECOVERY)
        self.assertEqual(re.findall(r"\b\d{6}\b", text), ["482910"])


@override_settings(DEFAULT_FROM_EMAIL=FROM, BREVO_API_KEY="")
class DevelopmentSendingTests(SimpleTestCase):
    def test_without_a_brevo_key_it_uses_django_mail_with_an_html_part(self):
        recovery._send_code_email(USER, "482910", RecoveryCode.Purpose.EMAIL_VERIFY)
        message = mail.outbox[-1]
        self.assertEqual(message.to, ["amina@example.com"])
        self.assertIn("482910", message.body)
        self.assertEqual(message.alternatives[0][1], "text/html")


@override_settings(DEFAULT_FROM_EMAIL=FROM, BREVO_API_KEY="test-key")
class BrevoSendingTests(SimpleTestCase):
    def test_sends_html_and_text_with_a_plain_sender_address(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            recovery._send_code_email(USER, "482910", RecoveryCode.Purpose.EMAIL_VERIFY)
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload["sender"]["email"], "no-reply@signconnect.example")
        self.assertEqual(payload["sender"]["name"], "SignConnect")
        self.assertEqual(payload["to"][0]["email"], "amina@example.com")
        self.assertIn("482910", payload["htmlContent"])
        self.assertIn("482910", payload["textContent"])
        self.assertNotIn("templateId", payload)
        self.assertEqual(request.get_header("Api-key"), "test-key")

    def test_a_brevo_rejection_is_logged_and_raised(self):
        error = urllib.error.HTTPError("url", 403, "Forbidden", {}, None)
        error.read = lambda: b'{"message":"unrecognised IP address"}'
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertLogs("apps.accounts.recovery", "ERROR") as logs:
                with self.assertRaises(RuntimeError):
                    recovery._send_code_email(USER, "482910", RecoveryCode.Purpose.EMAIL_VERIFY)
        self.assertIn("403", logs.output[0])
        self.assertIn("unrecognised IP", logs.output[0])
        self.assertNotIn("482910", logs.output[0])
        self.assertNotIn("test-key", logs.output[0])
