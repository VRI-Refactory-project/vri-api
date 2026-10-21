from itertools import count

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase

from .models import InterpreterProfile, Specialisation
from .routing import pick_interpreter
from .specialisations import VALUES, primary_for
from .test_routing import NOW, make_interpreter

User = get_user_model()
_n = count(1)


def sign_up_body(**extra):
    n = next(_n)
    body = {
        "full_name": "Amina Interpreter",
        "phone_number": f"07731{n:05d}",
        "email": f"spec{n}@example.com",
        "password": "Sign-Connect-2026",
    }
    body.update(extra)
    return body


class LibraryEndpointTests(APITestCase):
    def test_lists_the_library_without_logging_in(self):
        response = self.client.get(reverse("interpreters:specialisation-library"))
        self.assertEqual(response.status_code, 200)
        values = [item["value"] for item in response.json()]
        self.assertEqual(tuple(values), VALUES)
        self.assertIn("EDUCATION", values)
        self.assertGreater(len(values), 4)


class RegistrationTests(APITestCase):
    url = "/api/v1/auth/register/interpreter/"

    def register(self, **extra):
        return self.client.post(self.url, sign_up_body(**extra), format="json")

    def test_several_specialisations(self):
        response = self.register(specialisations=["EDUCATION", "MEDICAL"])
        self.assertEqual(response.status_code, 201, response.content)
        profile = InterpreterProfile.objects.get()
        self.assertEqual(profile.specialisations, ["MEDICAL", "EDUCATION"])
        self.assertEqual(profile.specialisation, "MEDICAL")

    def test_library_only_choice_routes_as_general(self):
        self.register(specialisations=["EDUCATION"])
        profile = InterpreterProfile.objects.get()
        self.assertEqual(profile.specialisation, Specialisation.GENERAL)
        self.assertEqual(profile.specialisations, ["EDUCATION"])

    def test_old_single_field_still_works(self):
        response = self.register(specialisation="LEGAL")
        self.assertEqual(response.status_code, 201, response.content)
        profile = InterpreterProfile.objects.get()
        self.assertEqual(profile.specialisations, ["LEGAL"])
        self.assertEqual(profile.specialisation, "LEGAL")

    def test_defaults_to_general(self):
        self.register()
        self.assertEqual(InterpreterProfile.objects.get().specialisations, ["GENERAL"])

    def test_other_needs_text(self):
        response = self.register(specialisations=["OTHER"])
        self.assertEqual(response.status_code, 400)
        self.assertIn("specialisation_other", response.json())

    def test_other_with_text(self):
        response = self.register(specialisations=["OTHER"], specialisation_other="Farming")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(InterpreterProfile.objects.get().specialisation_other, "Farming")

    def test_unknown_value_is_rejected(self):
        response = self.register(specialisations=["WIZARDRY"])
        self.assertEqual(response.status_code, 400)

    def test_empty_list_is_rejected(self):
        response = self.register(specialisations=[])
        self.assertEqual(response.status_code, 400)


class ProfileUpdateTests(APITestCase):
    def setUp(self):
        self.profile = make_interpreter()
        self.client.force_authenticate(self.profile.user)
        self.url = reverse("interpreters:interpreter-profile-detail")

    def test_update_list_sets_the_primary(self):
        response = self.client.patch(self.url, {"specialisations": ["LEGAL", "SECURITY"]}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.specialisation, "LEGAL")
        self.assertEqual(response.json()["specialisations"], ["LEGAL", "SECURITY"])

    def test_legacy_profile_reads_as_a_list(self):
        InterpreterProfile.objects.filter(pk=self.profile.pk).update(specialisations=[], specialisation="MEDICAL")
        response = self.client.get(self.url)
        self.assertEqual(response.json()["specialisations"], ["MEDICAL"])

    def test_other_needs_text_on_update(self):
        response = self.client.patch(self.url, {"specialisations": ["OTHER"]}, format="json")
        self.assertEqual(response.status_code, 400)


class RoutingTests(APITestCase):
    def test_specialist_from_the_list_is_preferred(self):
        general = make_interpreter()
        multi = make_interpreter()
        multi.specialisations = ["EDUCATION", "MEDICAL"]
        multi.specialisation = "MEDICAL"
        multi.save()
        match = pick_interpreter(Specialisation.MEDICAL, now=NOW)
        self.assertEqual(match.interpreter, multi)
        self.assertFalse(match.matched_by_fallback)
        self.assertNotEqual(match.interpreter, general)

    def test_covering_medical_as_a_secondary_still_counts(self):
        make_interpreter()
        secondary = make_interpreter()
        secondary.specialisations = ["GENERAL", "MEDICAL"]
        secondary.save()
        match = pick_interpreter(Specialisation.MEDICAL, now=NOW)
        self.assertEqual(match.interpreter, secondary)
        self.assertFalse(match.matched_by_fallback)

    def test_no_specialist_falls_back(self):
        make_interpreter()
        self.assertTrue(pick_interpreter(Specialisation.LEGAL, now=NOW).matched_by_fallback)


class PrimaryTests(APITestCase):
    def test_primary_for(self):
        self.assertEqual(primary_for(["EDUCATION"]), "GENERAL")
        self.assertEqual(primary_for(["EDUCATION", "LEGAL"]), "LEGAL")
        self.assertEqual(primary_for(["OTHER"]), "OTHER")
