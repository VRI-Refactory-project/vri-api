"""V2-024: emergency requests are routed differently from normal ones.

Emergencies fail after a shorter wait, their offers expire sooner, they are offered to every eligible
interpreter at once (no waiting for a specialist), they send a different notification, and each person may
make a limited number of them in an hour. The priority cannot be changed after the request is created.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.interpreters.models import Specialisation
from apps.notifications.models import Notification
from apps.service_requests.models import RequestOffer, ServiceRequest
from apps.service_requests.services import create_request, fail_overdue_requests
from apps.service_requests.test_offers import make_interpreter, make_user

User = get_user_model()
EMERGENCY = ServiceRequest.Priority.EMERGENCY
NORMAL = ServiceRequest.Priority.NORMAL


def new_requester():
    return make_user(User.Role.DEAF_HOH)


def age(service_request, seconds):
    """Pretend the request was created this many seconds ago."""
    ServiceRequest.objects.filter(pk=service_request.pk).update(
        created_at=timezone.now() - timedelta(seconds=seconds)
    )


@override_settings(
    EMERGENCY_MAX_WAIT_SECONDS=120,
    REQUEST_MAX_WAIT_SECONDS=300,
    EMERGENCY_OFFER_EXPIRY_SECONDS=30,
    REQUEST_OFFER_EXPIRY_SECONDS=60,
    EMERGENCY_REQUESTS_PER_HOUR=3,
)
class EmergencyTimingTests(TestCase):
    def test_an_emergency_with_no_interpreter_fails_after_two_minutes(self):
        emergency, _ = create_request(requester=new_requester(), priority=EMERGENCY)
        age(emergency, 130)

        fail_overdue_requests()

        emergency.refresh_from_db()
        self.assertEqual(emergency.status, ServiceRequest.Status.FAILED)

    def test_a_normal_request_is_still_waiting_at_two_minutes(self):
        normal, _ = create_request(requester=new_requester(), priority=NORMAL)
        age(normal, 130)

        fail_overdue_requests()

        normal.refresh_from_db()
        self.assertEqual(normal.status, ServiceRequest.Status.PENDING)

    def test_a_normal_request_fails_after_five_minutes(self):
        normal, _ = create_request(requester=new_requester(), priority=NORMAL)
        age(normal, 310)

        fail_overdue_requests()

        normal.refresh_from_db()
        self.assertEqual(normal.status, ServiceRequest.Status.FAILED)

    def test_emergency_offers_expire_after_thirty_seconds(self):
        make_interpreter()
        emergency, _ = create_request(requester=new_requester(), priority=EMERGENCY)

        offer = RequestOffer.objects.get(service_request=emergency)
        self.assertEqual((offer.expires_at - offer.offered_at).total_seconds(), 30)

    def test_normal_offers_still_expire_after_sixty_seconds(self):
        make_interpreter()
        normal, _ = create_request(requester=new_requester(), priority=NORMAL)

        offer = RequestOffer.objects.get(service_request=normal)
        self.assertEqual((offer.expires_at - offer.offered_at).total_seconds(), 60)


@override_settings(EMERGENCY_REQUESTS_PER_HOUR=3)
class EmergencyRoutingTests(TestCase):
    def test_an_emergency_goes_to_every_eligible_interpreter_even_when_a_specialist_exists(self):
        specialist = make_interpreter(Specialisation.MEDICAL)
        general = make_interpreter(Specialisation.GENERAL)

        emergency, _ = create_request(
            requester=new_requester(),
            priority=EMERGENCY,
            specialisation_required=Specialisation.MEDICAL,
        )

        offers = RequestOffer.objects.filter(service_request=emergency)
        offered_to = set(offers.values_list("interpreter_id", flat=True))
        self.assertEqual(offered_to, {specialist.pk, general.pk})

    def test_a_normal_request_still_goes_only_to_the_specialist_when_one_exists(self):
        specialist = make_interpreter(Specialisation.MEDICAL)
        make_interpreter(Specialisation.GENERAL)

        normal, _ = create_request(
            requester=new_requester(),
            priority=NORMAL,
            specialisation_required=Specialisation.MEDICAL,
        )

        offered_to = list(RequestOffer.objects.filter(service_request=normal).values_list("interpreter_id", flat=True))
        self.assertEqual(offered_to, [specialist.pk])

    def test_interpreters_get_an_emergency_notification(self):
        interpreter = make_interpreter()

        create_request(requester=new_requester(), priority=EMERGENCY)

        notification = Notification.objects.filter(user=interpreter.user).latest("id")
        self.assertEqual(notification.title, "Emergency interpreting request")

    def test_interpreters_get_the_normal_notification_for_a_normal_request(self):
        interpreter = make_interpreter()

        create_request(requester=new_requester(), priority=NORMAL)

        notification = Notification.objects.filter(user=interpreter.user).latest("id")
        self.assertEqual(notification.title, "New interpreting request")


@override_settings(EMERGENCY_REQUESTS_PER_HOUR=3)
class EmergencyLimitTests(APITestCase):
    def setUp(self):
        self.url = reverse("service-request-list-create")
        self.requester = new_requester()
        self.client.force_authenticate(self.requester)

    def post(self, priority):
        return self.client.post(self.url, {"priority": priority}, format="json")

    def finish_request(self, service_request):
        """Close a request so the same person may make another (only one open request at a time)."""
        ServiceRequest.objects.filter(pk=service_request.pk).update(status=ServiceRequest.Status.CANCELLED)

    def test_three_emergencies_in_an_hour_are_allowed(self):
        for _ in range(3):
            response = self.post("EMERGENCY")
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            self.finish_request(ServiceRequest.objects.get(pk=response.data["id"]))

    def test_the_fourth_emergency_in_an_hour_is_refused(self):
        for _ in range(3):
            response = self.post("EMERGENCY")
            self.finish_request(ServiceRequest.objects.get(pk=response.data["id"]))

        response = self.post("EMERGENCY")

        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertEqual(response.data["code"], "emergency_limit_reached")

    def test_normal_requests_do_not_count_towards_the_limit(self):
        for _ in range(4):
            response = self.post("NORMAL")
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            self.finish_request(ServiceRequest.objects.get(pk=response.data["id"]))

    def test_emergencies_older_than_an_hour_do_not_count(self):
        for _ in range(3):
            response = self.post("EMERGENCY")
            service_request = ServiceRequest.objects.get(pk=response.data["id"])
            self.finish_request(service_request)
            ServiceRequest.objects.filter(pk=service_request.pk).update(
                created_at=timezone.now() - timedelta(hours=2)
            )

        self.assertEqual(self.post("EMERGENCY").status_code, status.HTTP_201_CREATED)


class PriorityLockTests(APITestCase):
    def test_the_priority_cannot_be_changed_after_the_request_is_created(self):
        requester = new_requester()
        service_request, _ = create_request(requester=requester, priority=NORMAL)
        self.client.force_authenticate(requester)
        url = reverse("service-request-detail", args=[service_request.pk])

        response = self.client.patch(url, {"priority": "EMERGENCY"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        service_request.refresh_from_db()
        self.assertEqual(service_request.priority, NORMAL)
