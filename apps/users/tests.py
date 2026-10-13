from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import UserProfile

User = get_user_model()


class DeafUserProfileTests(APITestCase):
    def setUp(self):
        # Create a test Deaf user using exact custom User model fields
        self.user = User.objects.create_user(
            phone_number="+256700000001",
            password="SecurePassword123!",
            email="micheal@vri.ug",
            role="deaf_hoh",
        )
        self.profile_url = reverse("deaf-user-profile-me")

    def test_get_deaf_user_profile_authenticated(self):
        """Ensure an authenticated user can retrieve their profile details."""
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self.profile_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["email"], "micheal@vri.ug")

    def test_patch_deaf_user_profile_success(self):
        """Ensure an authenticated user can update emergency contacts & accessibility settings."""
        self.client.force_authenticate(user=self.user)
        payload = {
            "preferred_name": "Micheal",
            "emergency_contact_name": "Sarah Kiggundu (Sister)",
            "emergency_contact_phone": "+256700123456",
            "flash_alerts": True,
            "live_captions": True,
            "preferred_domains": ["healthcare", "legal"],
        }
        response = self.client.patch(self.profile_url, data=payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["preferred_name"], "Micheal")
        self.assertEqual(
            response.data["emergency_contact_name"],
            "Sarah Kiggundu (Sister)",
        )
        self.assertEqual(response.data["emergency_contact_phone"], "+256700123456")
        self.assertTrue(response.data["flash_alerts"])
        self.assertTrue(response.data["live_captions"])
        self.assertEqual(response.data["preferred_domains"], ["healthcare", "legal"])

        # Verify persistence in database
        profile = UserProfile.objects.get(user=self.user)
        self.assertEqual(profile.emergency_contact_name, "Sarah Kiggundu (Sister)")

    def test_unauthenticated_access_denied(self):
        """Ensure unauthenticated users cannot access or update profile endpoints."""
        response = self.client.get(self.profile_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
