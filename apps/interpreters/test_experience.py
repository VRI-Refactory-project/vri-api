from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.service_requests.test_offers import make_interpreter

User = get_user_model()


class InterpreterExperienceTests(APITestCase):
    def setUp(self):
        self.interpreter = make_interpreter()
        self.other_interpreter = make_interpreter()
        self.url = reverse("interpreters:interpreter-profile-detail")
        self.client.force_authenticate(self.interpreter.user)

    def test_experience_is_null_until_entered_and_patch_round_trips(self):
        self.assertIsNone(self.client.get(self.url).data["years_experience"])

        response = self.client.patch(self.url, {"years_experience": 4}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["years_experience"], 4)
        self.interpreter.refresh_from_db()
        self.assertEqual(self.interpreter.years_experience_value, 4)
        self.assertIsNotNone(self.interpreter.years_experience_set_at)

    def test_experience_increases_after_full_years_elapsed(self):
        profile = self.interpreter
        profile.years_experience_value = 3
        profile.years_experience_set_at = timezone.now() - timedelta(days=370)
        profile.save(
            update_fields=["years_experience_value", "years_experience_set_at"]
        )

        response = self.client.get(self.url)

        self.assertEqual(response.data["years_experience"], 4)

    def test_negative_fractional_and_string_experience_are_rejected(self):
        for value in (-1, 2.5, "two"):
            response = self.client.patch(
                self.url, {"years_experience": value}, format="json"
            )
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_null_clears_experience(self):
        self.client.patch(self.url, {"years_experience": 5}, format="json")

        response = self.client.patch(
            self.url, {"years_experience": None}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["years_experience"])

    def test_patching_resets_the_experience_start_date(self):
        self.client.patch(self.url, {"years_experience": 2}, format="json")
        self.interpreter.refresh_from_db()
        old_set_at = self.interpreter.years_experience_set_at

        response = self.client.patch(self.url, {"years_experience": 7}, format="json")

        self.interpreter.refresh_from_db()
        self.assertEqual(response.data["years_experience"], 7)
        self.assertGreater(self.interpreter.years_experience_set_at, old_set_at)

    def test_profile_endpoint_only_updates_authenticated_interpreters_own_profile(self):
        self.client.force_authenticate(self.other_interpreter.user)

        response = self.client.patch(self.url, {"years_experience": 6}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.other_interpreter.refresh_from_db()
        self.interpreter.refresh_from_db()
        self.assertEqual(self.other_interpreter.years_experience_value, 6)
        self.assertIsNone(self.interpreter.years_experience_value)
