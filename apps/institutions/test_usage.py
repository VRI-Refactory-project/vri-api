"""V2-029: an institution administrator sees how much their organisation uses VRI."""

from datetime import date, datetime, time, timedelta

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.core.permissions import user_institution
from apps.service_requests.models import ServiceRequest
from apps.service_requests.test_offers import make_interpreter

from .models import StaffRole
from .test_sessions import make_staff
from .test_staff import make_institution

Status = ServiceRequest.Status
USAGE_URL = reverse("institution-usage")
TODAY = timezone.localdate()


def make_request(
    requester,
    day,
    request_status=Status.COMPLETED,
    kind="GENERAL",
    interpreter=None,
    minutes=None,
    hour=10,
):
    """A request made at `hour` o'clock (Kampala time) on `day`."""
    made = timezone.make_aware(datetime.combine(day, time(hour)))
    request = ServiceRequest.objects.create(
        requester=requester,
        # Tagged as create_request() does for staff (V2-013).
        institution=user_institution(requester),
        interpreter=interpreter,
        status=request_status,
        specialisation_required=kind,
        assigned_at=made if minutes else None,
        ended_at=made + timedelta(minutes=minutes) if minutes else None,
    )
    # created_at is set automatically on create, so it is moved to the day afterwards.
    ServiceRequest.objects.filter(pk=request.pk).update(created_at=made)
    return request


class InstitutionUsageTests(APITestCase):
    def setUp(self):
        self.admin = make_institution("0700000300", "Mulago Hospital")
        self.nurse = make_staff(self.admin, "Grace Achieng", "Maternity")
        self.doctor = make_staff(self.admin, "Dr Sarah Nakato", "Emergency")
        self.interpreter = make_interpreter()
        self.client.force_authenticate(self.admin)

    def get(self, **params):
        return self.client.get(USAGE_URL, params)

    def test_counts_sessions_types_outcomes_and_time(self):
        make_request(
            self.nurse, TODAY, kind="MEDICAL", interpreter=self.interpreter, minutes=30
        )
        make_request(
            self.nurse, TODAY, kind="MEDICAL", interpreter=self.interpreter, minutes=10
        )
        make_request(
            self.doctor, TODAY - timedelta(days=2), Status.CANCELLED, kind="LEGAL"
        )

        response = self.get()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data
        self.assertEqual(
            data["period"],
            {"from": str(TODAY - timedelta(days=6)), "to": str(TODAY)},
        )
        self.assertEqual(
            data["totals"],
            {
                "sessions": 3,
                "previous_period_sessions": 0,
                "completed": 2,
                "completion_rate": 67,
                "total_minutes": 40,
                "avg_duration_minutes": 20,
            },
        )
        self.assertEqual(
            [(row["type"], row["count"]) for row in data["by_type"]],
            [("GENERAL", 0), ("MEDICAL", 2), ("LEGAL", 1)],
        )
        self.assertEqual(
            [(row["status"], row["label"], row["count"]) for row in data["by_outcome"]],
            [("COMPLETED", "Completed", 2), ("CANCELLED", "Cancelled", 1)],
        )
        self.assertEqual(
            data["by_department"],
            [
                {"department": "Maternity", "count": 2},
                {"department": "Emergency", "count": 1},
            ],
        )
        self.assertEqual(len(data["over_time"]), 7)
        self.assertEqual(data["over_time"][-1], {"date": str(TODAY), "count": 2})
        self.assertEqual(data["over_time"][-3]["count"], 1)
        # The admin, the nurse and the doctor all have access; two of them made requests.
        self.assertEqual(data["staff"], {"total": 3, "used_vri": 2, "not_used": 1})

    def test_date_range_includes_both_days_and_compares_with_the_period_before(self):
        start, end = date(2026, 11, 1), date(2026, 11, 3)
        make_request(self.nurse, start, hour=0)
        make_request(self.nurse, end, hour=23)
        make_request(self.nurse, end + timedelta(days=1))
        make_request(self.nurse, start - timedelta(days=3))  # the previous 3 days
        make_request(self.nurse, start - timedelta(days=4))  # before that: not counted

        data = self.get(**{"from": "2026-11-01", "to": "2026-11-03"}).data

        self.assertEqual(data["totals"]["sessions"], 2)
        self.assertEqual(data["totals"]["previous_period_sessions"], 1)
        self.assertEqual(
            [row["date"] for row in data["over_time"]],
            ["2026-11-01", "2026-11-02", "2026-11-03"],
        )

    def test_staff_filter(self):
        make_request(self.nurse, TODAY)
        make_request(self.doctor, TODAY)

        data = self.get(staff=self.nurse.institution_staff.id).data

        self.assertEqual(data["totals"]["sessions"], 1)
        self.assertEqual(
            data["by_department"], [{"department": "Maternity", "count": 1}]
        )
        self.assertEqual(data["staff"], {"total": 1, "used_vri": 1, "not_used": 0})

    def test_department_filter(self):
        make_request(self.nurse, TODAY)
        make_request(self.doctor, TODAY)

        data = self.get(department="emergency").data

        self.assertEqual(data["totals"]["sessions"], 1)
        self.assertEqual(data["staff"]["total"], 1)

    def test_revoked_staff_still_count_in_sessions_but_not_in_staff(self):
        make_request(self.nurse, TODAY)
        membership = self.nurse.institution_staff
        membership.is_active = False
        membership.save(update_fields=["is_active"])

        data = self.get().data

        self.assertEqual(data["totals"]["sessions"], 1)
        self.assertEqual(data["staff"], {"total": 2, "used_vri": 0, "not_used": 2})

    def test_another_institutions_sessions_are_never_counted(self):
        other_admin = make_institution("0700000301", "Nsambya Hospital")
        make_request(make_staff(other_admin, "Peter Okello"), TODAY)

        data = self.get().data

        self.assertEqual(data["totals"]["sessions"], 0)
        self.assertEqual(data["staff"]["total"], 3)

    def test_no_sessions_gives_zeros(self):
        totals = self.get().data["totals"]

        self.assertEqual(totals["completion_rate"], 0)
        self.assertEqual(totals["avg_duration_minutes"], 0)

    def test_bad_periods_are_refused(self):
        for params, field in [
            ({"from": "14/11/2026"}, "from"),
            ({"to": "yesterday"}, "to"),
            ({"from": "2026-11-10", "to": "2026-11-01"}, "from"),
            ({"from": "2025-01-01", "to": "2026-11-01"}, "from"),
            ({"staff": "abc"}, "staff"),
        ]:
            with self.subTest(params=params):
                response = self.get(**params)
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(field, response.data)

    def test_staff_members_cannot_see_usage(self):
        self.client.force_authenticate(make_staff(self.admin, role=StaffRole.STAFF))

        self.assertEqual(self.get().status_code, status.HTTP_403_FORBIDDEN)
