"""V2-029: institution administrators download the Analytics page as a PDF, with the same filters."""

from datetime import date, timedelta
from unittest import mock

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.service_requests.models import ServiceRequest

from . import usage, usage_pdf
from .models import StaffRole
from .test_sessions import make_staff
from .test_staff import make_institution
from .test_usage import TODAY, make_request

EXPORT_URL = reverse("dashboard-institution-export")


class UsageExportTests(APITestCase):
    def setUp(self):
        self.admin = make_institution("0700000400", "Mulago Hospital")
        self.nurse = make_staff(self.admin, "Grace Achieng", "Maternity")
        self.doctor = make_staff(self.admin, "Dr Sarah Nakato", "Emergency")
        self.client.force_authenticate(self.admin)

    def export(self, **params):
        """The response, and what the PDF was built from."""
        with mock.patch.object(
            usage_pdf, "render_usage_pdf", wraps=usage_pdf.render_usage_pdf
        ) as render:
            response = self.client.get(EXPORT_URL, params)
        return response, render.call_args

    def test_downloads_a_pdf_named_after_the_institution_and_period(self):
        make_request(self.nurse, TODAY, kind="MEDICAL", minutes=20)

        response, built_from = self.export(**{"from": "2026-11-01", "to": "2026-11-07"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(
            response["Content-Disposition"],
            'attachment; filename="mulago-hospital-usage-2026-11-01-to-2026-11-07.pdf"',
        )
        self.assertTrue(response.content.startswith(b"%PDF-"))
        name, _, filters = built_from.args
        self.assertEqual(name, "Mulago Hospital")
        self.assertEqual(filters, "All staff, all departments")

    def test_the_pdf_uses_the_same_filters_as_the_screen(self):
        make_request(self.nurse, TODAY)
        make_request(self.doctor, TODAY)

        _, built_from = self.export(staff=self.nurse.institution_staff.id)

        _, figures, filters = built_from.args
        self.assertEqual(figures["totals"]["sessions"], 1)
        self.assertEqual(figures["staff"], {"total": 1, "used_vri": 1, "not_used": 0})
        self.assertEqual(filters, "Grace Achieng, all departments")

        _, built_from = self.export(department="Emergency")
        self.assertEqual(built_from.args[1]["totals"]["sessions"], 1)
        self.assertEqual(built_from.args[2], "All staff, Emergency")

    def test_bad_periods_are_refused(self):
        for params in (
            {"from": "14/11/2026"},
            {"from": "2026-11-10", "to": "2026-11-01"},
            {"staff": "abc"},
        ):
            with self.subTest(params=params):
                response, _ = self.export(**params)
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_staff_members_cannot_download_it(self):
        self.client.force_authenticate(make_staff(self.admin, role=StaffRole.STAFF))

        response, _ = self.export()

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class RenderUsagePdfTests(APITestCase):
    """The layout copes with every shape of data: nothing at all, a short period and a long one."""

    def setUp(self):
        self.admin = make_institution("0700000401", "St. Mary's <Lacor> Hospital")
        self.nurse = make_staff(self.admin, "Grace Achieng", "")

    def render(self, start, end):
        institution = self.admin.institution_staff.institution
        figures = usage.institution_usage(
            ServiceRequest.objects.filter(
                requester__institution_staff__institution=institution
            ),
            institution.staff.all(),
            start,
            end,
        )
        return usage_pdf.render_usage_pdf(
            "St. Mary's <Lacor> Hospital", figures, "All staff, all departments"
        )

    def test_empty_period(self):
        self.assertTrue(
            self.render(TODAY - timedelta(days=6), TODAY).startswith(b"%PDF-")
        )

    def test_long_period_with_sessions_and_no_department(self):
        make_request(self.nurse, TODAY, minutes=30)
        make_request(self.nurse, TODAY - timedelta(days=200), minutes=5)

        pdf = self.render(date(2025, 11, 20), TODAY)

        self.assertTrue(pdf.startswith(b"%PDF-"))
