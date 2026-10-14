"""V2-005: a platform administrator creates an approved institution, by API and in the admin site."""

import re
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.accounts.serializers import TokenPairSerializer
from apps.audit.models import AuditLog

from .models import InstitutionProfile, InstitutionStatus

PASSWORD = "Sign-Connect-2026"
NEW_PASSWORD = "Mulago-Own-2026!"
URL = reverse("manage-institution-create")
TEMPORARY_PASSWORD = re.compile(r"\b([A-Z2-9]{4}-[A-Z2-9]{4})\b")


def make_user(phone, role, **extra):
    return User.objects.create_user(phone, PASSWORD, full_name="Test User", role=role, **extra)


def institution_data(**overrides):
    data = {
        "organisation_name": "Mulago Hospital",
        "organisation_type": "HOSPITAL",
        "full_name": "Grace Namutebi",
        "phone_number": "0772555000",
        "email": "grace@mulago.example",
    }
    data.update(overrides)
    return data


def emailed_password():
    return TEMPORARY_PASSWORD.search(mail.outbox[-1].body).group(1)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class AdminCreateInstitutionApiTests(APITestCase):
    def setUp(self):
        cache.clear()  # reset rate limits
        self.admin = make_user("0700000001", User.Role.ADMIN, email="admin@signconnect.example")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {TokenPairSerializer.for_user(self.admin)['access']}")

    def test_creates_an_approved_institution_and_emails_the_login_details(self):
        res = self.client.post(URL, institution_data(), format="json")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data["institution"]["status"], "APPROVED")
        self.assertEqual(res.data["user"]["role"], "institution")
        self.assertTrue(res.data["user"]["must_change_password"])
        self.assertIn("gr***@mulago.example", res.data["detail"])
        self.assertNotIn("temporary_password", res.data)  # the admin never sees it

        profile = InstitutionProfile.objects.get()
        self.assertEqual(profile.status, InstitutionStatus.APPROVED)
        self.assertEqual(profile.user.email, "grace@mulago.example")
        self.assertIsNone(profile.user.terms_accepted_at)  # only the institution can accept the Terms

        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ["grace@mulago.example"])
        self.assertIn("Mulago Hospital", email.body)
        self.assertTrue(profile.user.check_password(emailed_password()))

    def test_creation_is_audited(self):
        self.client.post(URL, institution_data(), format="json")
        self.assertTrue(AuditLog.objects.filter(event=AuditLog.Event.INSTITUTION_CREATED, user=self.admin).exists())

    def test_only_platform_administrators(self):
        for role, phone in [(User.Role.INSTITUTION, "0700000002"), (User.Role.INTERPRETER, "0700000003"),
                            (User.Role.DEAF_HOH, "0700000004")]:
            with self.subTest(role=role):
                user = make_user(phone, role)
                self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {TokenPairSerializer.for_user(user)['access']}")
                self.assertEqual(self.client.post(URL, institution_data(), format="json").status_code, 403)
        self.client.credentials()
        self.assertEqual(self.client.post(URL, institution_data(), format="json").status_code, 401)
        self.assertFalse(InstitutionProfile.objects.exists())

    def test_same_validation_as_self_registration(self):
        self.client.post(URL, institution_data(), format="json")
        duplicate = self.client.post(URL, institution_data(email="other@mulago.example"), format="json")
        self.assertIn("phone_number", duplicate.data)
        no_email = self.client.post(URL, institution_data(phone_number="0772555001", email=""), format="json")
        self.assertIn("email", no_email.data)
        other = self.client.post(URL, institution_data(phone_number="0772555002", email="b@x.example",
                                                       organisation_type="OTHER"), format="json")
        self.assertIn("organisation_type_other", other.data)
        self.assertEqual(InstitutionProfile.objects.count(), 1)

    def test_password_and_terms_sent_in_the_request_are_ignored(self):
        res = self.client.post(URL, institution_data(password=PASSWORD, terms_accepted=True), format="json")
        self.assertEqual(res.status_code, 201)
        user = User.objects.get(email="grace@mulago.example")
        self.assertFalse(user.check_password(PASSWORD))
        self.assertIsNone(user.terms_accepted_at)

    def test_nothing_is_created_if_the_email_cannot_be_sent(self):
        with mock.patch("apps.institutions.credentials.send_mail", side_effect=OSError("smtp down")):
            res = self.client.post(URL, institution_data(), format="json")
        self.assertEqual(res.status_code, 503)
        self.assertFalse(User.objects.filter(email="grace@mulago.example").exists())
        self.assertFalse(InstitutionProfile.objects.exists())
        self.assertFalse(AuditLog.objects.filter(event=AuditLog.Event.INSTITUTION_CREATED).exists())

    def test_institution_signs_in_with_the_emailed_password_and_must_change_it(self):
        self.client.post(URL, institution_data(), format="json")
        self.client.credentials()
        res = self.client.post(reverse("auth-login"),
                               {"identifier": "grace@mulago.example", "password": emailed_password()}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["user"]["must_change_password"])
        self.assertEqual(res.data["user"]["institution_status"], "APPROVED")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.data['access']}")
        blocked = self.client.get(reverse("institution-me"))
        self.assertEqual(blocked.data["code"], "password_change_required")

        changed = self.client.post(reverse("auth-password-change"),
                                   {"current_password": emailed_password(), "new_password": NEW_PASSWORD},
                                   format="json")
        self.assertEqual(changed.status_code, 200, changed.data)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {changed.data['access']}")
        self.assertEqual(self.client.get(reverse("institution-me")).status_code, 200)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class AdminSiteCreateInstitutionTests(APITestCase):
    url = reverse("admin:institutions_institutionprofile_add")

    def setUp(self):
        self.admin = User.objects.create_superuser("0700000001", PASSWORD, full_name="Platform Admin",
                                                   email="admin@signconnect.example")
        self.client.force_login(self.admin)

    def test_add_page_shows_the_institution_form(self):
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Create institution and email login details")

    def test_add_creates_an_approved_institution_and_emails_it(self):
        res = self.client.post(self.url, institution_data())
        profile = InstitutionProfile.objects.get()
        self.assertRedirects(res, reverse("admin:institutions_institutionprofile_change", args=[profile.pk]))
        self.assertEqual(profile.status, InstitutionStatus.APPROVED)
        self.assertEqual(mail.outbox[0].to, ["grace@mulago.example"])

    def test_add_shows_validation_errors_and_creates_nothing(self):
        make_user("0772555000", User.Role.DEAF_HOH)
        res = self.client.post(self.url, institution_data())
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "already exists")
        self.assertFalse(InstitutionProfile.objects.exists())

    def test_add_reports_a_failed_email_and_creates_nothing(self):
        with mock.patch("apps.institutions.credentials.send_mail", side_effect=OSError("smtp down")):
            res = self.client.post(self.url, institution_data())
        self.assertContains(res, "could not be emailed")
        self.assertFalse(InstitutionProfile.objects.exists())
