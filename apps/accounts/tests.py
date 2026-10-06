from django.test import TestCase
from django.urls import reverse


class HealthCheckTests(TestCase):
    def test_health_returns_ok(self):
        response = self.client.get(reverse("health"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


class ApiDocsTests(TestCase):
    def test_swagger_ui_loads(self):
        self.assertEqual(self.client.get(reverse("swagger-ui")).status_code, 200)

    def test_schema_loads(self):
        self.assertEqual(self.client.get(reverse("schema")).status_code, 200)
