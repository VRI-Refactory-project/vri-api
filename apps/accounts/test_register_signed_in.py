"""Creating an account is only for people who are signed out."""

from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import User
from .serializers import TokenPairSerializer

PASSWORD = "Sign-Connect-2026"
REGISTER_ROUTES = ("auth-register-deaf", "auth-register-interpreter", "auth-register-institution", "user-register")


class RegisterWhileSignedInTests(APITestCase):
    def setUp(self):
        cache.clear()  # reset rate limits
        self.signed_in = User.objects.create_user(
            "0772999000", PASSWORD, full_name="Signed In", role=User.Role.ADMIN, email="signed.in@example.com"
        )

    def sign_in(self, user=None):
        token = TokenPairSerializer.for_user(user or self.signed_in)["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def register_deaf(self):
        return self.client.post(
            reverse("auth-register-deaf"),
            {"full_name": "Amina Nakato", "phone_number": "0772123456", "password": PASSWORD},
            format="json",
        )

    def test_a_signed_out_person_can_register(self):
        self.assertEqual(self.register_deaf().status_code, status.HTTP_201_CREATED)

    def test_a_signed_in_person_cannot_register(self):
        self.sign_in()

        response = self.register_deaf()

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["code"], "already_signed_in")
        self.assertFalse(User.objects.filter(full_name="Amina Nakato").exists())

    def test_every_registration_route_refuses_a_signed_in_person(self):
        self.sign_in()
        for route in REGISTER_ROUTES:
            with self.subTest(route=route):
                response = self.client.post(reverse(route), {}, format="json")
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
                self.assertEqual(response.data["code"], "already_signed_in")

    def test_an_old_or_broken_token_does_not_block_sign_up(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")

        self.assertEqual(self.register_deaf().status_code, status.HTTP_201_CREATED)

    def test_a_deactivated_accounts_token_does_not_block_sign_up(self):
        self.sign_in()
        self.signed_in.is_active = False
        self.signed_in.save(update_fields=["is_active"])

        self.assertEqual(self.register_deaf().status_code, status.HTTP_201_CREATED)
