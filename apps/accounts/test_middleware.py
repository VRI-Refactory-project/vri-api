from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

User = get_user_model()


class TestPreventSharedBrowserJWTMiddleware(APITestCase):
    def setUp(self):
        self.user_a = User.objects.create_user(
            phone_number="+256772111111",
            password="securepassword123",
            full_name="Test Interpreter",
            role=User.Role.INTERPRETER,
        )
        self.user_b = User.objects.create_user(
            phone_number="+256772222222",
            password="securepassword456",
            full_name="Test Institution",
            role=User.Role.INSTITUTION,
        )

        self.me_url = reverse("auth-me")

    def test_single_user_access_succeeds(self):
        """A user with a valid JWT and matching active cookie should browse normally."""
        refresh = RefreshToken.for_user(self.user_a)
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}"
        )
        self.client.cookies["active_platform_user"] = str(self.user_a.id)

        response = self.client.get(self.me_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_shared_browser_detects_different_user_and_blocks(self):
        """A different user should be blocked while another profile is active."""
        refresh = RefreshToken.for_user(self.user_b)
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}"
        )
        self.client.cookies["active_platform_user"] = str(self.user_a.id)

        response = self.client.get(self.me_url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn(
            "Another user profile is currently active",
            response.json()["detail"],
        )

    def test_back_arrow_cache_prevention_headers(self):
        """Authenticated responses should have strict anti-cache headers."""
        refresh = RefreshToken.for_user(self.user_a)
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}"
        )
        self.client.cookies["active_platform_user"] = str(self.user_a.id)

        response = self.client.get(self.me_url)

        self.assertEqual(
            response["Cache-Control"],
            "no-cache, no-store, must-revalidate, max-age=0",
        )
        self.assertEqual(response["Pragma"], "no-cache")
        self.assertEqual(response["Expires"], "0")
