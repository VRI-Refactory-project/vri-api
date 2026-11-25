from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.institutions.models import (
    InstitutionAlert,
    InstitutionDepartment,
    InstitutionProfile,
    InstitutionStaff,
    InstitutionStatus,
    StaffRole,
)
from apps.service_requests.models import ServiceRequest
from apps.service_requests.services import create_request, fail_overdue_requests
from apps.service_requests.test_offers import make_user

User = get_user_model()


class InstitutionFailureAlertTests(APITestCase):
    def setUp(self):
        self.admin = make_user(role=User.Role.INSTITUTION)
        self.institution = InstitutionProfile.objects.create(
            user=self.admin,
            organisation_name="Mulago Hospital",
            organisation_type="HOSPITAL",
            status=InstitutionStatus.APPROVED,
        )
        InstitutionStaff.objects.create(
            institution=self.institution,
            user=self.admin,
            role=StaffRole.ADMIN,
        )
        self.staff = make_user(role=User.Role.INSTITUTION)
        self.department = InstitutionDepartment.objects.create(
            institution=self.institution,
            name="Children Department",
        )
        InstitutionStaff.objects.create(
            institution=self.institution,
            user=self.staff,
            role=StaffRole.STAFF,
            department=self.department,
        )

    def test_timeout_creates_one_urgent_alert_for_the_requesting_institution(self):
        service_request, _ = create_request(requester=self.staff)
        now = timezone.now()
        ServiceRequest.objects.filter(pk=service_request.pk).update(
            created_at=now - timedelta(hours=1),
            institution=None,
        )

        self.assertEqual(fail_overdue_requests(now=now), 1)
        service_request.refresh_from_db()
        self.assertEqual(service_request.status, ServiceRequest.Status.FAILED)

        alert = InstitutionAlert.objects.get(source_request_id=service_request.pk)
        self.assertEqual(alert.institution, self.institution)
        self.assertEqual(alert.severity, InstitutionAlert.Severity.URGENT)
        self.assertEqual(
            alert.message,
            "A request for a General session has expired because no interpreter was available.",
        )
        self.assertEqual(fail_overdue_requests(now=now + timedelta(minutes=1)), 0)
        self.assertEqual(
            InstitutionAlert.objects.filter(source_request_id=service_request.pk).count(), 1
        )

    def test_emergency_alert_uses_department_name_without_repeating_department(self):
        create_request(
            requester=self.staff,
            priority=ServiceRequest.Priority.EMERGENCY,
        )

        alert = InstitutionAlert.objects.get(
            institution=self.institution,
            title="Emergency Request Started",
        )
        self.assertEqual(
            alert.message,
            "A staff member in Children Department just started an Emergency VRI session.",
        )

    def test_institution_admin_can_fetch_the_failure_alert(self):
        service_request, _ = create_request(requester=self.staff)
        now = timezone.now()
        ServiceRequest.objects.filter(pk=service_request.pk).update(
            created_at=now - timedelta(hours=1)
        )
        fail_overdue_requests(now=now)
        self.client.force_authenticate(self.admin)

        response = self.client.get(reverse("institution-alerts"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]["id"], InstitutionAlert.objects.get().pk)
