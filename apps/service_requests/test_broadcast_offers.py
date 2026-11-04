from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.interpreters.models import Specialisation
from apps.service_requests.models import RequestOffer, ServiceRequest
from apps.service_requests.services import OfferNotActive, create_request, respond_to_offer
from apps.service_requests.test_offers import make_interpreter, make_user

User = get_user_model()
PENDING = RequestOffer.Outcome.PENDING


def new_request(**kwargs):
    kwargs.setdefault("specialisation_required", Specialisation.GENERAL)
    request, _ = create_request(requester=make_user(User.Role.DEAF_HOH), **kwargs)
    return request


def offer_for(service_request, interpreter):
    return RequestOffer.objects.get(service_request=service_request, interpreter=interpreter)


class BroadcastAcceptTests(APITestCase):
    def test_the_first_to_accept_gets_the_request_and_the_others_close(self):
        first = make_interpreter()
        second = make_interpreter()
        request = new_request()
        first_offer = offer_for(request, first)
        second_offer = offer_for(request, second)

        respond_to_offer(first_offer, "accept")

        request.refresh_from_db()
        self.assertEqual(request.status, ServiceRequest.Status.ASSIGNED)
        self.assertEqual(request.interpreter, first)
        second_offer.refresh_from_db()
        self.assertEqual(second_offer.outcome, RequestOffer.Outcome.WITHDRAWN)
        with self.assertRaises(OfferNotActive):
            respond_to_offer(second_offer, "accept")

    def test_a_late_accept_is_refused_with_a_clear_message(self):
        first = make_interpreter()
        second = make_interpreter()
        request = new_request()
        second_offer = offer_for(request, second)
        respond_to_offer(offer_for(request, first), "accept")
        self.client.force_authenticate(second.user)

        response = self.client.post(
            reverse("interpreter-offer-respond", kwargs={"pk": second_offer.pk}),
            {"action": "accept"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["code"], "offer_not_active")
        self.assertEqual(response.data["detail"], "This request is no longer available.")

    def test_accepting_closes_the_interpreters_other_open_offers(self):
        busy = make_interpreter()
        taken = new_request()
        other = new_request()
        free = make_interpreter()  # arrives after both requests were made

        respond_to_offer(offer_for(taken, busy), "accept")

        busy.refresh_from_db()
        self.assertFalse(busy.is_available)
        self.assertEqual(offer_for(other, busy).outcome, RequestOffer.Outcome.WITHDRAWN)
        # Nobody else holds that request any more, so it goes out again to the interpreter who is free.
        other.refresh_from_db()
        self.assertEqual(other.status, ServiceRequest.Status.OFFERED)
        self.assertEqual(offer_for(other, free).outcome, PENDING)


class BroadcastDeclineTests(APITestCase):
    def test_declining_leaves_the_request_open_for_the_others(self):
        first = make_interpreter()
        second = make_interpreter()
        request = new_request()

        respond_to_offer(offer_for(request, first), "decline")

        request.refresh_from_db()
        self.assertEqual(request.status, ServiceRequest.Status.OFFERED)
        self.assertIsNone(request.interpreter)
        self.assertEqual(offer_for(request, first).outcome, RequestOffer.Outcome.DECLINED)
        self.assertEqual(offer_for(request, second).outcome, PENDING)

    def test_the_request_waits_again_only_once_everyone_has_declined(self):
        first = make_interpreter()
        second = make_interpreter()
        request = new_request()

        respond_to_offer(offer_for(request, first), "decline")
        respond_to_offer(offer_for(request, second), "decline")

        request.refresh_from_db()
        # Both were asked and both said no, so nobody is offered it again.
        self.assertEqual(request.status, ServiceRequest.Status.PENDING)
        self.assertIsNone(request.interpreter)
