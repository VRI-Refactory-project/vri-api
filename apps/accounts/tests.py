from django.contrib.auth import authenticate, get_user_model
from django.contrib.staticfiles import finders
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from rest_framework import serializers
from rest_framework.test import APITestCase

from .serializers import BaseRegistrationSerializer

User = get_user_model()
PASSWORD = "Sign-Connect-2026"


def make_user(phone="0772123456", email=None, role=User.Role.DEAF_HOH, **extra):
    return User.objects.create_user(phone, PASSWORD, full_name="Test User", email=email, role=role, **extra)


class HealthAndDocsTests(TestCase):
    def test_health_reports_database(self):
        response = self.client.get(reverse("health"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "database": "ok"})

    def test_swagger_and_schema_load(self):
        self.assertEqual(self.client.get(reverse("swagger-ui")).status_code, 200)
        self.assertEqual(self.client.get(reverse("schema")).status_code, 200)

    def test_docs_ui_is_served_locally_not_from_a_cdn(self):
        for name in ("swagger-ui", "redoc"):
            html = self.client.get(reverse(name)).content.decode()
            self.assertNotIn("cdn.jsdelivr.net", html, name)
            self.assertIn("/static/drf_spectacular_sidecar/", html, name)
        self.assertIsNotNone(finders.find("drf_spectacular_sidecar/swagger-ui-dist/swagger-ui-bundle.js"))


class UserModelTests(TestCase):
    def test_local_phone_is_stored_in_e164(self):
        user = make_user(phone="0772 123456")
        user.refresh_from_db()
        self.assertEqual(str(user.phone_number), "+256772123456")

    def test_duplicate_phone_is_blocked_by_database(self):
        make_user(phone="0772123456")
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_user(phone="+256772123456")

    def test_users_without_email_do_not_clash(self):
        make_user(phone="0772123456")
        make_user(phone="0701234567")
        self.assertEqual(User.objects.filter(email__isnull=True).count(), 2)

    def test_email_is_lowercased(self):
        user = make_user(email="Sarah@Example.COM")
        self.assertEqual(user.email, "sarah@example.com")

    def test_password_is_hashed_with_argon2(self):
        user = make_user()
        self.assertTrue(user.password.startswith("argon2"))

    def test_superuser_is_admin_role(self):
        admin = User.objects.create_superuser("0700000001", PASSWORD, full_name="Admin")
        self.assertEqual(admin.role, User.Role.ADMIN)
        self.assertTrue(admin.is_staff and admin.is_superuser)

    def test_backend_accepts_phone_or_email(self):
        user = make_user(email="sarah@example.com")
        self.assertEqual(authenticate(username="0772123456", password=PASSWORD), user)
        self.assertEqual(authenticate(username="SARAH@example.com", password=PASSWORD), user)
        self.assertIsNone(authenticate(username="0772123456", password="wrong-password"))


class LoginApiTests(APITestCase):
    def setUp(self):
        cache.clear()  # reset login rate limit between tests
        self.user = make_user(email="sarah@example.com", role=User.Role.INTERPRETER)
        self.url = reverse("auth-login")

    def login(self, identifier, password=PASSWORD):
        return self.client.post(self.url, {"identifier": identifier, "password": password}, format="json")

    def test_login_with_local_phone(self):
        response = self.login("0772123456")
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
        self.assertEqual(response.data["user"]["role"], "interpreter")
        self.assertEqual(response.data["user"]["phone_number"], "+256772123456")

    def test_login_with_international_phone_and_email(self):
        self.assertEqual(self.login("+256772123456").status_code, 200)
        self.assertEqual(self.login("Sarah@Example.com").status_code, 200)

    def test_wrong_password_and_unknown_account_give_same_error(self):
        wrong = self.login("0772123456", "wrong-password")
        unknown = self.login("0799999999")
        self.assertEqual(wrong.status_code, 400)
        self.assertEqual(wrong.data, unknown.data)

    def test_deactivated_account_cannot_sign_in(self):
        self.user.is_active = False
        self.user.save()
        response = self.login("0772123456")
        self.assertEqual(response.status_code, 400)
        self.assertIn("deactivated", str(response.data))

    def test_me_requires_token(self):
        self.assertEqual(self.client.get(reverse("auth-me")).status_code, 401)
        access = self.login("0772123456").data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        response = self.client.get(reverse("auth-me"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["full_name"], "Test User")

    def test_refresh_returns_new_access_token(self):
        refresh = self.login("0772123456").data["refresh"]
        response = self.client.post(reverse("auth-token-refresh"), {"refresh": refresh}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)

    def test_login_is_rate_limited(self):
        for _ in range(10):
            self.login("0772123456", "wrong-password")
        self.assertEqual(self.login("0772123456").status_code, 429)


class ExampleRegistrationSerializer(BaseRegistrationSerializer):
    role = User.Role.INTERPRETER
    email_required = True
    city = serializers.CharField()

    def create_profile(self, user, validated_data):
        if validated_data["city"] == "fail":
            raise RuntimeError("profile creation failed")


class BaseRegistrationSerializerTests(TestCase):
    def data(self, **overrides):
        base = {"full_name": "Sarah N", "phone_number": "0772123456", "email": "sarah@example.com",
                "password": PASSWORD, "city": "Kampala"}
        base.update(overrides)
        return base

    def test_creates_user_with_role(self):
        s = ExampleRegistrationSerializer(data=self.data())
        self.assertTrue(s.is_valid(), s.errors)
        user = s.save()
        self.assertEqual(user.role, "interpreter")
        self.assertEqual(s.data["phone_number"], "+256772123456")
        self.assertNotIn("password", s.data)

    def test_duplicate_phone_rejected_with_clear_message(self):
        make_user(phone="+256772123456")
        s = ExampleRegistrationSerializer(data=self.data())
        self.assertFalse(s.is_valid())
        self.assertIn("already exists", str(s.errors["phone_number"]))

    def test_invalid_phone_rejected(self):
        s = ExampleRegistrationSerializer(data=self.data(phone_number="12345"))
        self.assertFalse(s.is_valid())
        self.assertIn("phone_number", s.errors)

    def test_email_required_when_flag_set(self):
        data = self.data()
        del data["email"]
        s = ExampleRegistrationSerializer(data=data)
        self.assertFalse(s.is_valid())
        self.assertIn("email", s.errors)

    def test_weak_or_common_password_rejected(self):
        for weak in ("123", "123456", "password"):
            s = ExampleRegistrationSerializer(data=self.data(password=weak))
            self.assertFalse(s.is_valid(), weak)
            self.assertIn("password", s.errors)

    def test_six_digit_pin_accepted(self):
        s = ExampleRegistrationSerializer(data=self.data(password="482913"))
        self.assertTrue(s.is_valid(), s.errors)

    def test_failed_profile_rolls_back_user(self):
        s = ExampleRegistrationSerializer(data=self.data(city="fail"))
        self.assertTrue(s.is_valid(), s.errors)
        with self.assertRaises(RuntimeError):
            s.save()
        self.assertFalse(User.objects.filter(phone_number="+256772123456").exists())
