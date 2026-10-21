from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework.test import APITestCase

User = get_user_model()
PASSWORD = "Logout-Pass-2026"


class LogoutTests(APITestCase):
    def setUp(self):
        cache.clear()
        User.objects.create_user("0772123456", PASSWORD, full_name="Sarah N", role=User.Role.DEAF_HOH)
        login = self.client.post(
            "/api/v1/auth/login/", {"identifier": "0772123456", "password": PASSWORD}, format="json"
        )
        self.refresh = login.json()["refresh"]

    def refresh_status(self, token):
        return self.client.post("/api/v1/auth/token/refresh/", {"refresh": token}, format="json").status_code

    def test_refresh_works_before_logout(self):
        self.assertEqual(self.refresh_status(self.refresh), 200)

    def test_logout_revokes_the_refresh_token(self):
        response = self.client.post("/api/v1/auth/logout/", {"refresh": self.refresh}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.refresh_status(self.refresh), 401)

    def test_logging_out_twice_is_fine(self):
        for _ in range(2):
            response = self.client.post("/api/v1/auth/logout/", {"refresh": self.refresh}, format="json")
            self.assertEqual(response.status_code, 200)

    def test_garbage_token_gets_the_same_answer(self):
        response = self.client.post("/api/v1/auth/logout/", {"refresh": "not-a-token"}, format="json")
        self.assertEqual(response.status_code, 200)

    def test_missing_token_gets_the_same_answer(self):
        self.assertEqual(self.client.post("/api/v1/auth/logout/", {}, format="json").status_code, 200)
