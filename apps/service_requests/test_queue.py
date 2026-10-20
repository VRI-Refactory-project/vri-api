from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.interpreters.models import Specialisation
from apps.service_requests.models import RequestOffer, ServiceRequest
from apps.service_requests.services import (
    create_request,
    dispatch_pending,
    expire_stale_offers,
    fail_overdue_requests,
    refresh_request,
    sweep,
)
from apps.service_requests.test_offers import make_interpreter, make_user

User = get_user_model()


def new_request(user=None, **kwargs):
    user = user or make_user(User.Role.DEAF_HOH)
    kwargs.setdefault("specialisation_required", Specialisation.GENERAL)
    request, _ = create_request(requester=user, **kwargs)
    return request


def let_offer_run_out(service_request):
    """Pretend the interpreter has not answered for longer than the offer allows."""
    RequestOffer.objects.filter(service_request=service_request).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )


def make_overdue(service_request):
    ServiceRequest.objects.filter(pk=service_request.pk).update(
        created_at=timezone.now() - timedelta(hours=1)
    )


class OfferExpiryTests(APITestCase):
    def test_an_unanswered_offer_goes_to_the_next_interpreter(self):
        first = make_interpreter()
        second = make_interpreter()
        request = new_request()
        self.assertEqual(request.interpreter, first)

        let_offer_run_out(request)
        refresh_request(request)

        request.refresh_from_db()
        self.assertEqual(request.status, ServiceRequest.Status.OFFERED)
        self.assertEqual(request.interpreter, second)
        outcomes = {o.interpreter_id: o.outcome for o in request.offers.all()}
        self.assertEqual(outcomes[first.pk], RequestOffer.Outcome.EXPIRED)
        self.assertEqual(outcomes[second.pk], RequestOffer.Outcome.PENDING)

    def test_the_same_interpreter_is_not_asked_twice(self):
        only = make_interpreter()
        request = new_request()

        let_offer_run_out(request)
        refresh_request(request)

        request.refresh_from_db()
        self.assertEqual(request.status, ServiceRequest.Status.PENDING)
        self.assertIsNone(request.interpreter)
        self.assertEqual(request.offers.filter(interpreter=only).count(), 1)

    def test_an_offer_still_in_time_is_left_alone(self):
        make_interpreter()
        request = new_request()

        expired = expire_stale_offers()

        request.refresh_from_db()
        self.assertEqual(expired, 0)
        self.assertEqual(request.status, ServiceRequest.Status.OFFERED)

    def test_an_expired_offer_frees_the_interpreter_for_other_requests(self):
        only = make_interpreter()
        first = new_request()
        let_offer_run_out(first)
        refresh_request(first)

        second = new_request()

        self.assertEqual(second.status, ServiceRequest.Status.OFFERED)
        self.assertEqual(second.interpreter, only)

    def test_an_interpreter_looking_at_their_offers_does_not_see_an_expired_one(self):
        interpreter = make_interpreter()
        request = new_request()
        let_offer_run_out(request)
        self.client.force_authenticate(interpreter.user)

        response = self.client.get(reverse("interpreter-offers"))

        self.assertEqual(response.data, [])


class WaitingTooLongTests(APITestCase):
    def test_a_request_that_waited_too_long_fails(self):
        request = new_request()
        make_overdue(request)

        refresh_request(request)

        request.refresh_from_db()
        self.assertEqual(request.status, ServiceRequest.Status.FAILED)
        self.assertEqual(request.failure_reason, "no_interpreter_available")
        self.assertIsNotNone(request.failed_at)

    def test_a_request_past_its_time_fails_even_if_an_interpreter_is_free(self):
        request = new_request()
        make_overdue(request)
        interpreter = make_interpreter()

        refresh_request(request)

        request.refresh_from_db()
        self.assertEqual(request.status, ServiceRequest.Status.FAILED)
        self.assertEqual(interpreter.request_offers.count(), 0)

    @override_settings(REQUEST_MAX_WAIT_SECONDS=10)
    def test_the_waiting_time_can_be_changed_in_settings(self):
        request = new_request()
        ServiceRequest.objects.filter(pk=request.pk).update(
            created_at=timezone.now() - timedelta(seconds=30)
        )

        self.assertEqual(fail_overdue_requests(), 1)

    def test_a_request_that_is_still_in_time_keeps_waiting(self):
        request = new_request()

        fail_overdue_requests()

        request.refresh_from_db()
        self.assertEqual(request.status, ServiceRequest.Status.PENDING)

    def test_a_failed_request_does_not_stop_the_person_asking_again(self):
        user = make_user(User.Role.DEAF_HOH)
        old = new_request(user)
        make_overdue(old)

        again = new_request(user)

        old.refresh_from_db()
        self.assertEqual(old.status, ServiceRequest.Status.FAILED)
        self.assertEqual(again.status, ServiceRequest.Status.PENDING)
        self.assertNotEqual(again.pk, old.pk)

    def test_the_app_sees_the_failure_when_it_asks(self):
        user = make_user(User.Role.DEAF_HOH)
        request = new_request(user)
        make_overdue(request)
        self.client.force_authenticate(user)

        response = self.client.get(
            reverse("service-request-detail", kwargs={"pk": request.pk})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "FAILED")

    def test_mine_no_longer_returns_a_request_that_has_failed(self):
        user = make_user(User.Role.DEAF_HOH)
        request = new_request(user)
        make_overdue(request)
        self.client.force_authenticate(user)

        response = self.client.get(reverse("service-request-mine"))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_asking_for_the_status_moves_the_queue_on(self):
        user = make_user(User.Role.DEAF_HOH)
        request = new_request(user)
        self.assertEqual(request.status, ServiceRequest.Status.PENDING)
        interpreter = make_interpreter()  # becomes free while the person waits
        self.client.force_authenticate(user)

        response = self.client.get(
            reverse("service-request-detail", kwargs={"pk": request.pk})
        )

        self.assertEqual(response.data["status"], "OFFERED")
        request.refresh_from_db()
        self.assertEqual(request.interpreter, interpreter)


class DispatchTests(APITestCase):
    def test_waiting_requests_are_offered_when_an_interpreter_becomes_free(self):
        request = new_request()
        self.assertEqual(request.status, ServiceRequest.Status.PENDING)
        interpreter = make_interpreter()

        offered = dispatch_pending()

        request.refresh_from_db()
        self.assertEqual(offered, 1)
        self.assertEqual(request.interpreter, interpreter)

    def test_an_emergency_goes_first_even_if_it_came_later(self):
        routine = new_request()
        emergency = new_request(priority=ServiceRequest.Priority.EMERGENCY)
        interpreter = make_interpreter()

        dispatch_pending()

        routine.refresh_from_db()
        emergency.refresh_from_db()
        self.assertEqual(emergency.interpreter, interpreter)
        self.assertEqual(routine.status, ServiceRequest.Status.PENDING)

    def test_the_longest_waiting_request_goes_first(self):
        older = new_request()
        newer = new_request()
        ServiceRequest.objects.filter(pk=older.pk).update(
            created_at=timezone.now() - timedelta(minutes=2)
        )
        interpreter = make_interpreter()

        dispatch_pending()

        older.refresh_from_db()
        newer.refresh_from_db()
        self.assertEqual(older.interpreter, interpreter)
        self.assertEqual(newer.status, ServiceRequest.Status.PENDING)

    def test_a_sweep_reports_what_it_did(self):
        interpreter = make_interpreter()
        first = new_request()  # offered to the only interpreter, who never answers
        waiting = new_request()  # waiting behind it
        failing = new_request()
        make_overdue(failing)
        let_offer_run_out(first)

        result = sweep()

        self.assertEqual(result, {"expired": 1, "failed": 1, "offered": 1})
        first.refresh_from_db()
        waiting.refresh_from_db()
        failing.refresh_from_db()
        self.assertEqual(failing.status, ServiceRequest.Status.FAILED)
        # The interpreter who let the first offer run out is not asked about it again, so they are
        # offered the request that was waiting instead.
        self.assertEqual(first.status, ServiceRequest.Status.PENDING)
        self.assertEqual(waiting.interpreter, interpreter)

    def test_the_command_runs_a_sweep(self):
        new_request()
        out = StringIO()

        call_command("dispatch_requests", stdout=out)

        self.assertIn("expired=0 offered=0 failed=0", out.getvalue())


class HealthSweepTests(APITestCase):
    def test_off_by_default(self):
        request = new_request()
        make_overdue(request)

        response = self.client.get("/api/health/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        request.refresh_from_db()
        self.assertEqual(request.status, ServiceRequest.Status.PENDING)

    @override_settings(DISPATCH_REQUESTS_ON_HEALTH=True)
    def test_when_switched_on_the_health_check_moves_the_queue(self):
        request = new_request()
        make_overdue(request)

        response = self.client.get("/api/health/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        request.refresh_from_db()
        self.assertEqual(request.status, ServiceRequest.Status.FAILED)

    @override_settings(DISPATCH_REQUESTS_ON_HEALTH=True)
    def test_a_broken_sweep_never_breaks_the_health_check(self):
        with (
            patch("config.urls.sweep", side_effect=RuntimeError("boom")),
            self.assertLogs("config.urls", level="ERROR"),
        ):
            response = self.client.get("/api/health/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
