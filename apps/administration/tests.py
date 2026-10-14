from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

User = get_user_model()


class AdminSettingsTests(APITestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            email="admin@signconnect.ug",
            full_name="System Admin",
            phone_number="+256700000000",
            password="OldPassword123!",
        )
        self.client.force_authenticate(user=self.admin_user)
        self.settings_url = reverse("administration:admin-settings")
        self.password_url = reverse("administration:admin-change-password")

    def test_get_admin_settings_creates_profile(self):
        response = self.client.get(self.settings_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["full_name"], "System Admin")
        self.assertTrue(response.data["session_alerts"])

    def test_update_admin_profile_and_notifications(self):
        payload = {
            "full_name": "Updated Admin Name",
            "session_alerts": False,
            "verification_alerts": True,
        }
        response = self.client.patch(self.settings_url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["full_name"], "Updated Admin Name")
        self.assertFalse(response.data["session_alerts"])

        # Check persistence on subsequent request
        get_res = self.client.get(self.settings_url)
        self.assertEqual(get_res.data["full_name"], "Updated Admin Name")

    def test_change_password_updates_timestamp(self):
        payload = {
            "old_password": "OldPassword123!",
            "new_password": "NewSecurePassword456!",
            "confirm_password": "NewSecurePassword456!",
        }
        response = self.client.post(self.password_url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.admin_user.refresh_from_db()
        self.assertTrue(self.admin_user.check_password("NewSecurePassword456!"))
