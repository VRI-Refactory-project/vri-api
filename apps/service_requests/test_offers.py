from datetime import timedelta
from itertools import count

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.interpreters.models import (
    InterpreterAvailability,
    InterpreterProfile,
    Specialisation,
    VerificationStatus,
)
from apps.notifications.models import Notification
from apps.service_requests.models import RequestOffer, ServiceRequest

User = get_user_model()
_numbers = count(1)


def make_user(role):
    n = next(_numbers)
    return User.objects.create_user(
        f"+2567002{n:05d}",
        "Sign-Connect-2026",
        full_name=f"Person {n}",
        email=f"offers{n}@example.com",
        role=role,
    )


def make_interpreter(specialisation=Specialisation.GENERAL):
    """An approved interpreter who is free right now."""
    profile = InterpreterProfile.objects.create(
        user=make_user(User.Role.INTERPRETER),
        verification_status=VerificationStatus.APPROVED,
        is_available=True,
        specialisation=specialisation,
    )
    now = timezone.now()
    InterpreterAvailability.objects.create(
        interpreter=profile,
        start_at=now - timedelta(hours=1),
        end_at=now + timedelta(hours=1),
    )
    return profile


class OfferFlowTests(APITestCase):
    def setUp(self):
        self.deaf = make_user(User.Role.DEAF_HOH)
        self.client.force_authenticate(self.deaf)
        self.url = reverse("service-request-list-create")

    def create(self, **body):
        body.setdefault("specialisation_required", "GENERAL")
        return self.client.post(self.url, body, format="json")

    def test_stays_pending_when_nobody_is_available(self):
        response = self.create()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], "PENDING")

    def test_asks_the_specialist_first(self):
        make_interpreter(Specialisation.GENERAL)
        medical = make_interpreter(Specialisation.MEDICAL)

        response = self.create(specialisation_required="MEDICAL")

        found = ServiceRequest.objects.get(pk=response.data["id"])
        self.assertEqual(found.interpreter, medical)
        self.assertFalse(found.matched_by_fallback)

    def test_falls_back_to_a_general_interpreter_and_says_so(self):
        general = make_interpreter(Specialisation.GENERAL)

        response = self.create(specialisation_required="LEGAL")

        found = ServiceRequest.objects.get(pk=response.data["id"])
        self.assertEqual(found.status, ServiceRequest.Status.OFFERED)
        self.assertEqual(found.interpreter, general)
        self.assertTrue(found.matched_by_fallback)

    def test_every_offer_is_recorded(self):
        interpreter = make_interpreter()

        response = self.create()

        offer = RequestOffer.objects.get(service_request_id=response.data["id"])
        self.assertEqual(offer.interpreter, interpreter)
        self.assertEqual(offer.outcome, RequestOffer.Outcome.PENDING)
        self.assertGreater(offer.expires_at, offer.offered_at)

    def test_the_notification_carries_the_context(self):
        interpreter = make_interpreter(Specialisation.MEDICAL)

        self.create(specialisation_required="MEDICAL", priority="EMERGENCY")

        note = Notification.objects.get(user=interpreter.user)
        self.assertEqual(note.data["context"], "MEDICAL")
        self.assertEqual(note.data["priority"], "EMERGENCY")

    def test_rejects_an_unknown_priority(self):
        self.assertEqual(
            self.create(priority="URGENTISH").status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_only_one_open_request_at_a_time(self):
        self.create()

        response = self.create()

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["code"], "open_request_exists")
        self.assertEqual(ServiceRequest.objects.count(), 1)

    def test_a_retry_with_the_same_key_is_the_same_request(self):
        first = self.create(request_key="abc")

        again = self.create(request_key="abc")

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(again.status_code, status.HTTP_200_OK)
        self.assertEqual(again.data["id"], first.data["id"])
        self.assertEqual(ServiceRequest.objects.count(), 1)

    def test_an_interpreter_holds_one_offer_at_a_time(self):
        make_interpreter()
        self.create()

        self.client.force_authenticate(make_user(User.Role.DEAF_HOH))
        response = self.create()

        self.assertEqual(response.data["status"], "PENDING")

    def test_the_longest_waiting_interpreter_is_asked_first(self):
        first = make_interpreter()
        second = make_interpreter()

        one = self.create()
        self.client.post(
            reverse("service-request-cancel", kwargs={"pk": one.data["id"]})
        )
        two = self.create()

        # The first interpreter was offered the earlier request, so the second is next.
        self.assertEqual(
            ServiceRequest.objects.get(pk=one.data["id"]).offers.get().interpreter,
            first,
        )
        self.assertEqual(
            RequestOffer.objects.get(service_request_id=two.data["id"]).interpreter,
            second,
        )

    def test_only_the_requester_can_see_a_request(self):
        request_id = self.create().data["id"]
        url = reverse("service-request-detail", kwargs={"pk": request_id})

        self.assertEqual(self.client.get(url).status_code, status.HTTP_200_OK)

        self.client.force_authenticate(make_user(User.Role.DEAF_HOH))
        self.assertEqual(self.client.get(url).status_code, status.HTTP_404_NOT_FOUND)

    def test_looking_at_the_request_marks_the_requester_as_still_there(self):
        request_id = self.create().data["id"]
        self.assertIsNone(ServiceRequest.objects.get(pk=request_id).last_seen_at)

        self.client.get(reverse("service-request-detail", kwargs={"pk": request_id}))

        self.assertIsNotNone(ServiceRequest.objects.get(pk=request_id).last_seen_at)

    def test_mine_returns_the_open_request_or_nothing(self):
        mine = reverse("service-request-mine")
        self.assertEqual(self.client.get(mine).status_code, status.HTTP_204_NO_CONTENT)

        request_id = self.create().data["id"]

        response = self.client.get(mine)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], request_id)

    def test_the_requester_sees_the_interpreter_only_once_assigned(self):
        interpreter = make_interpreter()
        interpreter.sign_languages = ["Ugandan Sign Language"]
        interpreter.save()
        request_id = self.create().data["id"]
        url = reverse("service-request-detail", kwargs={"pk": request_id})
        self.assertIsNone(self.client.get(url).data["interpreter"])

        ServiceRequest.objects.filter(pk=request_id).update(
            status=ServiceRequest.Status.ASSIGNED
        )

        shown = self.client.get(url).data["interpreter"]
        self.assertEqual(shown["name"], interpreter.user.full_name)
        self.assertEqual(shown["languages"], "Ugandan Sign Language")

    def test_cancelling_frees_the_interpreter_and_keeps_the_history(self):
        interpreter = make_interpreter()
        request_id = self.create().data["id"]

        response = self.client.post(
            reverse("service-request-cancel", kwargs={"pk": request_id})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "CANCELLED")
        offer = RequestOffer.objects.get(service_request_id=request_id)
        self.assertEqual(offer.outcome, RequestOffer.Outcome.WITHDRAWN)

        # The interpreter can be offered a new request straight away.
        again = self.create()
        self.assertEqual(again.data["status"], "OFFERED")
        self.assertEqual(
            ServiceRequest.objects.get(pk=again.data["id"]).interpreter, interpreter
        )

    def test_cancelling_twice_is_fine(self):
        request_id = self.create().data["id"]
        url = reverse("service-request-cancel", kwargs={"pk": request_id})
        self.client.post(url)

        self.assertEqual(self.client.post(url).status_code, status.HTTP_200_OK)

    def test_an_assigned_request_cannot_be_cancelled(self):
        request_id = self.create().data["id"]
        ServiceRequest.objects.filter(pk=request_id).update(
            status=ServiceRequest.Status.ASSIGNED
        )

        response = self.client.post(
            reverse("service-request-cancel", kwargs={"pk": request_id})
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)


class InterpreterOffersApiTests(APITestCase):
    def test_an_interpreter_sees_what_was_offered_to_them_with_its_context(self):
        interpreter = make_interpreter(Specialisation.MEDICAL)
        other = make_interpreter(Specialisation.GENERAL)
        deaf = make_user(User.Role.DEAF_HOH)
        self.client.force_authenticate(deaf)
        self.client.post(
            reverse("service-request-list-create"),
            {"specialisation_required": "MEDICAL", "priority": "EMERGENCY"},
            format="json",
        )
        url = reverse("interpreter-offers")

        self.client.force_authenticate(interpreter.user)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["specialisation_required"], "MEDICAL")
        self.assertEqual(response.data[0]["priority"], "EMERGENCY")
        self.assertEqual(response.data[0]["requester_name"], deaf.full_name)
        self.assertIsNotNone(response.data[0]["expires_at"])

        self.client.force_authenticate(other.user)
        self.assertEqual(self.client.get(url).data, [])

    def test_only_interpreters_can_look(self):
        self.client.force_authenticate(make_user(User.Role.DEAF_HOH))

        response = self.client.get(reverse("interpreter-offers"))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
