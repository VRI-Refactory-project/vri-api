"""Institution administrators see their staff's sessions and bookings; staff edit their own profile."""

from datetime import date, time, timedelta
from itertools import count

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import User
from apps.audit.models import AuditLog
from apps.core.permissions import user_institution
from apps.interpreters.models import ScheduledSession
from apps.realtime.models import SessionFeedback, VideoSession
from apps.service_requests.models import ServiceRequest
from apps.service_requests.test_offers import make_interpreter

from .models import InstitutionDepartment, InstitutionStaff, InstitutionStatus, StaffRole
from .test_staff import PASSWORD, make_institution

Status = ServiceRequest.Status
SESSIONS_URL = reverse("institution-sessions")
SCHEDULED_URL = reverse("institution-scheduled-sessions")
PROFILE_URL = reverse("institution-my-profile")

_numbers = count(1)


def make_staff(
    admin, name="Dr Sarah Nakato", department="Emergency", role=StaffRole.STAFF
):
    """A staff member of `admin`'s institution."""
    n = next(_numbers)
    user = User.objects.create_user(
        f"+2567003{n:05d}",
        PASSWORD,
        full_name=name,
        role=User.Role.INSTITUTION,
        email=f"staff{n}@example.com",
    )
    department_record = None
    if department:
        department_record, _ = InstitutionDepartment.objects.get_or_create(
            institution=admin.institution_staff.institution,
            name=department,
        )
    InstitutionStaff.objects.create(
        institution=admin.institution_staff.institution,
        user=user,
        role=role,
        job_title=department,
        department=department_record,
    )
    return user


def make_request(requester, request_status, interpreter=None, minutes=None):
    started = timezone.now() - timedelta(hours=1)
    return ServiceRequest.objects.create(
        requester=requester,
        # Tagged as create_request() does for staff (V2-013).
        institution=user_institution(requester),
        interpreter=interpreter,
        status=request_status,
        assigned_at=started if interpreter else None,
        ended_at=started + timedelta(minutes=minutes) if minutes else None,
    )


class InstitutionSessionListTests(APITestCase):
    def setUp(self):
        self.admin = make_institution("0700000200", "Mulago Hospital")
        self.nurse = make_staff(self.admin, "Grace Achieng", "Maternity")
        self.doctor = make_staff(self.admin, "Dr Sarah Nakato", "Emergency")
        self.interpreter = make_interpreter()
        self.client.force_authenticate(self.admin)

    def ids(self, response):
        return {row["id"] for row in response.data}

    def test_admin_sees_every_staff_members_sessions(self):
        finished = make_request(
            self.nurse, Status.COMPLETED, self.interpreter, minutes=25
        )
        waiting = make_request(self.doctor, Status.PENDING)

        response = self.client.get(SESSIONS_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.ids(response), {finished.pk, waiting.pk})
        row = next(r for r in response.data if r["id"] == finished.pk)
        self.assertEqual(
            row["staff"],
            {
                "id": self.nurse.institution_staff.id,
                "name": "Grace Achieng",
                "department": "Maternity",
            },
        )
        self.assertEqual(row["interpreter_name"], self.interpreter.user.full_name)
        self.assertEqual(row["duration_minutes"], 25)

    def test_another_institutions_sessions_never_appear(self):
        other_admin = make_institution("0700000300", "Nsambya Hospital")
        make_request(make_staff(other_admin), Status.PENDING)
        mine = make_request(self.nurse, Status.PENDING)

        response = self.client.get(SESSIONS_URL)

        self.assertEqual(self.ids(response), {mine.pk})

    def test_deaf_users_requests_are_not_included(self):
        deaf = User.objects.create_user(
            "+256700399999", PASSWORD, full_name="Deaf Person", role=User.Role.DEAF_HOH
        )
        make_request(deaf, Status.PENDING)

        self.assertEqual(self.client.get(SESSIONS_URL).data, [])

    def test_revoked_staff_sessions_stay_in_the_history(self):
        old = make_request(self.nurse, Status.COMPLETED, self.interpreter, minutes=10)
        self.nurse.institution_staff.revoked_at = timezone.now()
        self.nurse.institution_staff.save()

        self.assertEqual(self.ids(self.client.get(SESSIONS_URL)), {old.pk})

    def test_interpreter_is_hidden_until_they_take_the_request(self):
        make_request(self.nurse, Status.OFFERED, interpreter=None)

        self.assertIsNone(self.client.get(SESSIONS_URL).data[0]["interpreter_name"])

    def test_state_filter(self):
        live = make_request(self.nurse, Status.ASSIGNED, self.interpreter)
        done = make_request(self.doctor, Status.COMPLETED, self.interpreter, minutes=5)

        self.assertEqual(
            self.ids(self.client.get(SESSIONS_URL, {"state": "active"})), {live.pk}
        )
        self.assertEqual(
            self.ids(self.client.get(SESSIONS_URL, {"state": "history"})), {done.pk}
        )
        self.assertEqual(
            self.client.get(SESSIONS_URL, {"state": "soon"}).status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_department_staff_and_search_filters(self):
        nurse_request = make_request(self.nurse, Status.PENDING)
        doctor_request = make_request(self.doctor, Status.PENDING)

        by_department = self.client.get(SESSIONS_URL, {"department": "maternity"})
        by_staff = self.client.get(
            SESSIONS_URL, {"staff": self.doctor.institution_staff.id}
        )
        by_search = self.client.get(SESSIONS_URL, {"search": "nakato"})

        self.assertEqual(self.ids(by_department), {nurse_request.pk})
        self.assertEqual(self.ids(by_staff), {doctor_request.pk})
        self.assertEqual(self.ids(by_search), {doctor_request.pk})
        self.assertEqual(
            self.client.get(SESSIONS_URL, {"staff": "abc"}).status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_reading_the_list_does_not_change_requests(self):
        waiting = make_request(self.nurse, Status.PENDING)
        before = waiting.updated_at

        self.client.get(SESSIONS_URL)

        waiting.refresh_from_db()
        self.assertEqual(waiting.updated_at, before)

    def test_pending_institutions_are_refused(self):
        pending_admin = make_institution(
            "0700000400", "New Clinic", status=InstitutionStatus.PENDING
        )
        self.client.force_authenticate(pending_admin)

        self.assertEqual(
            self.client.get(SESSIONS_URL).status_code, status.HTTP_403_FORBIDDEN
        )


def detail_url(session):
    return reverse("institution-session-detail", args=[session.pk])


class SessionHistoryTests(APITestCase):
    """V2-030: past sessions, metadata only. Staff see their own; administrators see everyone's."""

    def setUp(self):
        self.admin = make_institution("0700000700", "Mulago Hospital")
        self.nurse = make_staff(self.admin, "Grace Achieng", "Maternity")
        self.doctor = make_staff(self.admin, "Dr Sarah Nakato", "Emergency")
        self.interpreter = make_interpreter()
        self.nurses = make_request(self.nurse, Status.COMPLETED, self.interpreter, minutes=20)
        self.doctors = make_request(self.doctor, Status.COMPLETED, self.interpreter, minutes=35)

    def ids(self, response):
        return {row["id"] for row in response.data}

    def test_staff_see_only_their_own_sessions(self):
        self.client.force_authenticate(self.nurse)

        response = self.client.get(SESSIONS_URL, {"state": "history"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.ids(response), {self.nurses.pk})

    def test_staff_cannot_reach_a_colleagues_session_even_with_filters(self):
        self.client.force_authenticate(self.nurse)
        doctor_staff_id = self.doctor.institution_staff.id

        response = self.client.get(SESSIONS_URL, {"staff": doctor_staff_id})

        self.assertEqual(self.ids(response), set())

    def test_administrators_still_see_every_staff_members_sessions(self):
        self.client.force_authenticate(self.admin)

        response = self.client.get(SESSIONS_URL, {"state": "history"})

        self.assertEqual(self.ids(response), {self.nurses.pk, self.doctors.pk})

    def test_staff_open_their_own_session(self):
        self.client.force_authenticate(self.nurse)

        response = self.client.get(detail_url(self.nurses))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["duration_minutes"], 20)
        self.assertEqual(response.data["interpreter_name"], self.interpreter.user.full_name)
        self.assertEqual(response.data["staff"]["name"], "Grace Achieng")

    def test_a_colleagues_session_is_not_found_for_staff(self):
        self.client.force_authenticate(self.nurse)

        response = self.client.get(detail_url(self.doctors))

        # 404, not 403: staff cannot even learn that it exists.
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_administrators_open_any_of_their_institutions_sessions(self):
        self.client.force_authenticate(self.admin)

        self.assertEqual(self.client.get(detail_url(self.doctors)).status_code, status.HTTP_200_OK)

    def test_another_institutions_session_is_not_found(self):
        other_admin = make_institution("0700000800", "Nsambya Hospital")
        theirs = make_request(make_staff(other_admin), Status.COMPLETED, self.interpreter, minutes=5)
        self.client.force_authenticate(self.admin)

        self.assertEqual(self.client.get(detail_url(theirs)).status_code, status.HTTP_404_NOT_FOUND)

    def test_people_outside_institutions_are_refused(self):
        self.client.force_authenticate(self.interpreter.user)

        self.assertEqual(self.client.get(SESSIONS_URL).status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(
            self.client.get(detail_url(self.nurses)).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_channel_says_whether_the_session_was_on_demand_or_booked(self):
        booked = make_request(self.nurse, Status.COMPLETED, self.interpreter, minutes=30)
        ScheduledSession.objects.create(
            deaf_user=self.nurse,
            institution=user_institution(self.nurse),
            date=date.today(),
            time=time(10, 0),
            duration=30,
            session_type="medical",
            service_request=booked,
        )
        self.client.force_authenticate(self.nurse)

        rows = {row["id"]: row for row in self.client.get(SESSIONS_URL).data}

        self.assertEqual(rows[self.nurses.pk]["channel"], "on_demand")
        self.assertEqual(rows[booked.pk]["channel"], "booked")

    def test_only_metadata_is_returned_never_what_was_said(self):
        video = VideoSession.objects.create(service_request=self.nurses)
        SessionFeedback.objects.create(
            video_session=video,
            interpreter=self.interpreter,
            submitted_by=self.nurse,
            rating=2,
            comment="PRIVATE: the patient described her symptoms",
        )
        self.client.force_authenticate(self.nurse)

        listed = self.client.get(SESSIONS_URL).data[0]
        opened = self.client.get(detail_url(self.nurses)).data

        expected = {
            "id",
            "staff",
            "channel",
            "interpreter_name",
            "specialisation_required",
            "priority",
            "status",
            "display_status",
            "created_at",
            "assigned_at",
            "ended_at",
            "duration_minutes",
        }
        for body in (listed, opened):
            self.assertEqual(set(body), expected)
            self.assertNotIn("PRIVATE", str(body))


class InstitutionScheduledSessionTests(APITestCase):
    def setUp(self):
        self.admin = make_institution("0700000500", "Mulago Hospital")
        self.nurse = make_staff(self.admin, "Grace Achieng", "Maternity")

    def book(self, user, day):
        self.client.force_authenticate(user)
        return self.client.post(
            reverse("interpreters:schedule-session"),
            {
                "date": day.isoformat(),
                "time": "10:00",
                "duration": 30,
                "session_type": "medical",
            },
            format="json",
        )

    def test_staff_bookings_belong_to_their_institution(self):
        response = self.book(self.nurse, date.today() + timedelta(days=2))

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        booking = ScheduledSession.objects.get(pk=response.data["id"])
        self.assertEqual(booking.institution, self.admin.institution_staff.institution)

    def test_deaf_user_bookings_have_no_institution(self):
        deaf = User.objects.create_user(
            "+256700599999", PASSWORD, full_name="Deaf Person", role=User.Role.DEAF_HOH
        )
        response = self.book(deaf, date.today() + timedelta(days=2))

        self.assertIsNone(
            ScheduledSession.objects.get(pk=response.data["id"]).institution
        )

    def test_admin_lists_upcoming_bookings_soonest_first(self):
        institution = self.admin.institution_staff.institution
        later = ScheduledSession.objects.create(
            deaf_user=self.nurse,
            institution=institution,
            date=date.today() + timedelta(days=5),
            time=time(9),
            duration=30,
        )
        sooner = ScheduledSession.objects.create(
            deaf_user=self.nurse,
            institution=institution,
            date=date.today() + timedelta(days=1),
            time=time(9),
            duration=30,
        )
        ScheduledSession.objects.create(  # already past
            deaf_user=self.nurse,
            institution=institution,
            date=date.today() - timedelta(days=1),
            time=time(9),
            duration=30,
        )
        other_admin = make_institution("0700000600", "Nsambya Hospital")
        ScheduledSession.objects.create(
            deaf_user=make_staff(other_admin),
            institution=other_admin.institution_staff.institution,
            date=date.today() + timedelta(days=1),
            time=time(9),
            duration=30,
        )
        self.client.force_authenticate(self.admin)

        response = self.client.get(SCHEDULED_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row["id"] for row in response.data], [sooner.pk, later.pk])
        self.assertEqual(response.data[0]["staff"]["department"], "Maternity")

    def test_staff_who_are_not_administrators_are_refused(self):
        self.client.force_authenticate(self.nurse)

        self.assertEqual(
            self.client.get(SCHEDULED_URL).status_code, status.HTTP_403_FORBIDDEN
        )


class MyStaffProfileTests(APITestCase):
    def setUp(self):
        self.admin = make_institution("0700000700", "Mulago Hospital")
        self.nurse = make_staff(self.admin, "Grace Achieng", "Maternity")
        self.client.force_authenticate(self.nurse)

    def test_staff_see_their_own_profile(self):
        response = self.client.get(PROFILE_URL)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["full_name"], "Grace Achieng")
        self.assertEqual(response.data["job_title"], "Maternity")

    def test_staff_change_their_name_and_phone(self):
        response = self.client.patch(
            PROFILE_URL,
            {"full_name": "  Grace   Achieng Okello ", "phone_number": "+256772555000"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.nurse.refresh_from_db()
        self.assertEqual(self.nurse.full_name, "Grace Achieng Okello")
        self.assertEqual(str(self.nurse.phone_number), "+256772555000")
        self.assertTrue(
            AuditLog.objects.filter(
                event=AuditLog.Event.STAFF_UPDATED,
                user=self.nurse,
                target_user=self.nurse,
            ).exists()
        )

    def test_keeping_the_same_phone_number_is_allowed(self):
        response = self.client.patch(
            PROFILE_URL, {"phone_number": str(self.nurse.phone_number)}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_another_accounts_phone_number_is_refused(self):
        response = self.client.patch(
            PROFILE_URL, {"phone_number": str(self.admin.phone_number)}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("phone_number", response.data)

    def test_name_follows_the_sign_up_rule(self):
        response = self.client.patch(PROFILE_URL, {"full_name": "12345"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_job_title_role_and_email_cannot_be_changed_here(self):
        email = self.nurse.email
        self.client.patch(
            PROFILE_URL,
            {"job_title": "Director", "role": "ADMIN", "email": "new@example.com"},
            format="json",
        )

        membership = InstitutionStaff.objects.get(user=self.nurse)
        self.nurse.refresh_from_db()
        self.assertEqual(membership.job_title, "Maternity")
        self.assertEqual(membership.role, StaffRole.STAFF)
        self.assertEqual(self.nurse.email, email)

    def test_other_roles_are_refused(self):
        deaf = User.objects.create_user(
            "+256700799999", PASSWORD, full_name="Deaf Person", role=User.Role.DEAF_HOH
        )
        self.client.force_authenticate(deaf)

        self.assertEqual(
            self.client.get(PROFILE_URL).status_code, status.HTTP_403_FORBIDDEN
        )
