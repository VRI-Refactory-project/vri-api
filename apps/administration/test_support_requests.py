from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.administration.models import SupportRequest
from apps.institutions.models import InstitutionProfile, InstitutionStaff, StaffRole

User = get_user_model()
CREATE_URL = "/api/v1/support/requests/"
LIST_URL = "/api/v1/dashboard/admin/support-requests/"
PASSWORD = "StrongPassword123!"


def user(role, phone, name):
    return User.objects.create_user(phone, PASSWORD, role=role, full_name=name)


class SupportRequestTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = user(User.Role.ADMIN, "0707000001", "System Admin")
        self.deaf = user(User.Role.DEAF_HOH, "0707000002", "Amina Deaf")
        self.interpreter = user(User.Role.INTERPRETER, "0707000003", "Irene Interpreter")
        self.institution_user = user(User.Role.INSTITUTION, "0707000004", "Org Admin")
        self.institution = InstitutionProfile.objects.create(
            user=self.institution_user, organisation_name="Central Hospital", organisation_type="HOSPITAL"
        )
        InstitutionStaff.objects.create(
            institution=self.institution, user=self.institution_user, role=StaffRole.ADMIN
        )
        self.staff_user = user(User.Role.INSTITUTION, "0707000005", "Org Staff")
        InstitutionStaff.objects.create(
            institution=self.institution, user=self.staff_user, role=StaffRole.STAFF
        )

    def create_as(self, account, data=None):
        self.client.force_authenticate(account)
        payload = data or {
            "subject": "Need help",
            "category": "Account",
            "message": "Please help with my account.",
        }
        return self.client.post(CREATE_URL, payload, format="json")

    def test_create_roles_identity_and_ignore_spoofed_identity(self):
        for account, expected_role, expected_membership in (
            (self.deaf, "deaf_hoh", None),
            (self.interpreter, "interpreter", None),
            (self.institution_user, "institution", "ADMIN"),
            (self.staff_user, "institution", "STAFF"),
        ):
            response = self.create_as(account, {
                "subject": "Need help", "category": "Account", "message": "Please help with my account.",
                "requester": self.admin.pk, "requester_role": "admin",
            })
            self.assertEqual(response.status_code, 201)
            saved = SupportRequest.objects.get(pk=response.data["id"])
            self.assertEqual(saved.requester_id, account.pk)
            self.assertEqual(saved.requester_role, expected_role)
            self.assertEqual(saved.status, "OPEN")
            self.assertEqual(saved.requester_institution_role, expected_membership or "")
            self.assertEqual(saved.requester_institution_name, "Central Hospital" if expected_membership else "")

    def test_create_auth_and_role_permissions(self):
        self.assertEqual(self.client.post(CREATE_URL, {}, format="json").status_code, 401)
        for account in (self.admin,):
            self.assertEqual(self.create_as(account).status_code, 403)

    def test_subject_and_message_lengths_are_validated(self):
        for field, value in (("subject", "x"), ("subject", "s" * 201), ("message", "short"), ("message", "m" * 5001)):
            payload = {"subject": "Good subject", "category": "Account", "message": "A sufficiently long message."}
            payload[field] = value
            response = self.create_as(self.deaf, payload)
            self.assertEqual(response.status_code, 400)
            self.assertIn(field, response.data)

    def test_admin_filtering_pagination_and_requester_identity(self):
        rows = []
        for index in range(23):
            rows.append(
                SupportRequest.objects.create(
                    requester=self.deaf,
                    requester_name=self.deaf.full_name,
                    requester_role=self.deaf.role,
                    subject=f"Issue {index}",
                    category="Account",
                    message="Detailed support message.",
                    status="RESOLVED" if index == 0 else "OPEN",
                )
            )
        self.client.force_authenticate(self.admin)
        page = self.client.get(LIST_URL, {"status": "OPEN", "page": 1, "page_size": 10})
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.data["count"], 22)
        self.assertEqual(len(page.data["results"]), 10)
        self.assertIsNotNone(page.data["next"])
        self.assertIsNone(page.data["previous"])
        self.assertEqual(page.data["results"][0]["id"], rows[-1].id)
        self.assertEqual(page.data["results"][0]["requester"], {
            "user_id": self.deaf.pk, "name": "Amina Deaf", "role": "deaf_hoh"
        })
        self.assertEqual(self.client.get(LIST_URL, {"status": "RESOLVED"}).data["count"], 1)
        self.assertEqual(self.client.get(LIST_URL, {"status": "IN_PROGRESS"}).data["count"], 0)
        self.assertEqual(self.client.get(LIST_URL).data["count"], 23)

    def test_admin_update_only_changes_status_and_response_fields(self):
        request = SupportRequest.objects.create(
            requester=self.deaf,
            requester_name=self.deaf.full_name,
            requester_role=self.deaf.role,
            subject="Issue",
            category="Other",
            message="Do not change this original content.",
        )
        self.client.force_authenticate(self.admin)
        response = self.client.patch(f"{LIST_URL}{request.pk}/", {
            "status": "IN_PROGRESS", "admin_response": "We are reviewing it.", "follow_up": "Check back tomorrow.",
            "message": "Tampered", "requester": self.admin.pk,
        }, format="json")
        self.assertEqual(response.status_code, 200)
        request.refresh_from_db()
        self.assertEqual(request.status, "IN_PROGRESS")
        self.assertEqual(request.admin_response, "We are reviewing it.")
        self.assertEqual(request.follow_up, "Check back tomorrow.")
        self.assertEqual(request.message, "Do not change this original content.")
        self.assertEqual(request.requester_id, self.deaf.pk)

    def test_admin_routes_are_private_and_admin_only(self):
        request = SupportRequest.objects.create(
            requester=self.deaf,
            requester_name=self.deaf.full_name,
            requester_role=self.deaf.role,
            subject="Private",
            category="Other",
            message="Private message content.",
        )
        self.assertEqual(self.client.get(LIST_URL).status_code, 401)
        for account in (self.deaf, self.interpreter, self.institution_user, self.staff_user):
            self.client.force_authenticate(account)
            self.assertEqual(self.client.get(LIST_URL).status_code, 403)
            response = self.client.patch(
                f"{LIST_URL}{request.pk}/", {"status": "RESOLVED"}, format="json"
            )
            self.assertEqual(response.status_code, 403)
        # There is no user-facing list or detail route; only the creator endpoint is exposed.
        self.client.force_authenticate(self.deaf)
        self.assertEqual(self.client.get(CREATE_URL).status_code, 405)
