from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.interpreters.models import InterpreterProfile, VerificationStatus

from .models import User

PASSWORD = "Sign-Connect-2026"


class RegistrationEndpointTests(APITestCase):
    def setUp(self):
        cache.clear()  # reset rate limits

    def deaf(self, **overrides):
        data = {
            "full_name": "Amina Nakato",
            "phone_number": "0772123456",
            "password": PASSWORD,
        }
        data.update(overrides)
        return data

    def interpreter(self, **overrides):
        data = {
            "full_name": "Ruth Auma",
            "phone_number": "0772000111",
            "email": "ruth@example.com",
            "password": PASSWORD,
        }
        data.update(overrides)
        return data

    def test_deaf_registration_creates_account_and_signs_in(self):
        res = self.client.post(
            reverse("auth-register-deaf"), self.deaf(), format="json"
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.assertIn("access", res.data)
        self.assertIn("refresh", res.data)
        self.assertEqual(res.data["user"]["role"], "deaf_hoh")
        self.assertEqual(res.data["user"]["phone_number"], "+256772123456")
        self.assertTrue(User.objects.filter(phone_number="+256772123456").exists())
        # The returned token works straight away.
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
        self.assertEqual(self.client.get(reverse("auth-me")).status_code, 200)

    def test_deaf_registration_does_not_require_email(self):
        res = self.client.post(
            reverse("auth-register-deaf"), self.deaf(), format="json"
        )
        self.assertEqual(res.status_code, 201)
        self.assertIsNone(res.data["user"]["email"])

    def test_new_user_can_log_in_with_phone_and_password(self):
        self.client.post(reverse("auth-register-deaf"), self.deaf(), format="json")
        res = self.client.post(
            reverse("auth-login"),
            {"identifier": "0772123456", "password": PASSWORD},
            format="json",
        )
        self.assertEqual(res.status_code, 200)

    def test_duplicate_phone_is_rejected_and_no_second_account_created(self):
        self.client.post(reverse("auth-register-deaf"), self.deaf(), format="json")
        res = self.client.post(
            reverse("auth-register-deaf"), self.deaf(), format="json"
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("phone_number", res.data)
        self.assertEqual(User.objects.count(), 1)

    def test_weak_password_is_rejected(self):
        res = self.client.post(
            reverse("auth-register-deaf"), self.deaf(password="123"), format="json"
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("password", res.data)
        self.assertFalse(User.objects.exists())

    def test_password_needs_eight_characters_a_number_and_a_special_character(self):
        cases = {
            "Ab1!": "too short",
            "Abcdefgh!": "no number",
            "Abcdefgh1": "no special character",
            "abcdefgh": "neither",
            "12345678": "digits only",
        }
        for password, why in cases.items():
            res = self.client.post(
                reverse("auth-register-deaf"),
                self.deaf(password=password),
                format="json",
            )
            self.assertEqual(res.status_code, 400, why)
            self.assertIn("password", res.data, why)
        self.assertFalse(User.objects.exists())

    def test_password_with_a_number_and_a_special_character_is_accepted(self):
        res = self.client.post(
            reverse("auth-register-deaf"),
            self.deaf(password="Hello-World9"),
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)

    def test_accepting_the_terms_stores_when(self):
        res = self.client.post(
            reverse("auth-register-deaf"), self.deaf(terms_accepted=True), format="json"
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.assertIsNotNone(User.objects.get().terms_accepted_at)
        self.assertNotIn("terms_accepted", res.data["user"])

    def test_no_terms_flag_stores_nothing(self):
        for i, flag in enumerate([{}, {"terms_accepted": False}]):
            res = self.client.post(
                reverse("auth-register-deaf"),
                self.deaf(phone_number=f"077212345{i}", **flag),
                format="json",
            )
            self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(
            User.objects.filter(terms_accepted_at__isnull=False).count(), 0
        )

    def test_cannot_choose_a_role_through_the_request(self):
        res = self.client.post(
            reverse("auth-register-deaf"), self.deaf(role="admin"), format="json"
        )
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["user"]["role"], "deaf_hoh")

    def test_interpreter_registration_creates_pending_profile(self):
        res = self.client.post(
            reverse("auth-register-interpreter"),
            self.interpreter(specialisation="MEDICAL"),
            format="json",
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["user"]["role"], "interpreter")
        profile = InterpreterProfile.objects.get(user__phone_number="+256772000111")
        self.assertEqual(profile.verification_status, VerificationStatus.INACTIVE)
        self.assertEqual(profile.specialisation, "MEDICAL")
        self.assertFalse(profile.is_available)

    def test_interpreter_specialisation_defaults_to_general(self):
        res = self.client.post(
            reverse("auth-register-interpreter"), self.interpreter(), format="json"
        )
        self.assertEqual(res.status_code, 201)
        self.assertEqual(InterpreterProfile.objects.get().specialisation, "GENERAL")

    def test_interpreter_email_is_required(self):
        data = self.interpreter()
        del data["email"]
        res = self.client.post(
            reverse("auth-register-interpreter"), data, format="json"
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("email", res.data)
        self.assertFalse(User.objects.exists())

    def test_interpreter_invalid_specialisation_rejected(self):
        res = self.client.post(
            reverse("auth-register-interpreter"),
            self.interpreter(specialisation="MAGIC"),
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("specialisation", res.data)

    def test_interpreter_can_upload_certificate_with_the_returned_token(self):
        import shutil
        import tempfile

        from django.core.files.uploadedfile import SimpleUploadedFile

        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        with override_settings(MEDIA_ROOT=media):
            res = self.client.post(
                reverse("auth-register-interpreter"), self.interpreter(), format="json"
            )
            self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
            up = self.client.post(
                reverse("interpreters:certification-upload"),
                {
                    "document": SimpleUploadedFile(
                        "c.pdf", b"%PDF-1.4 x", content_type="application/pdf"
                    ),
                    "certificate_type": "Sign language interpreter certificate",
                    "issuing_organisation": "Uganda National Association of the Deaf",
                },
                format="multipart",
            )
        self.assertEqual(up.status_code, 201, up.data)

    def test_interpreter_profile_endpoint_works_without_username(self):
        res = self.client.post(
            reverse("auth-register-interpreter"), self.interpreter(), format="json"
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
        me = self.client.get(reverse("interpreters:interpreter-profile-detail"))
        self.assertEqual(me.status_code, 200, me.data)
        self.assertEqual(me.data["full_name"], "Ruth Auma")
        self.assertEqual(me.data["verification_status"], "INACTIVE")
