"""V2-011: a signed-in app swaps its login for a short ticket to open a WebSocket."""

import time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import signing
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.realtime.tickets import TICKET_MAX_AGE_SECONDS, issue_ticket, user_from_ticket
from apps.service_requests.test_offers import make_user

User = get_user_model()


class TicketEndpointTests(APITestCase):
    def test_a_signed_in_user_gets_a_ticket_for_themselves(self):
        user = make_user(User.Role.DEAF_HOH)
        self.client.force_authenticate(user)

        response = self.client.post(reverse("realtime-ticket"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["expires_in"], TICKET_MAX_AGE_SECONDS)
        self.assertEqual(user_from_ticket(response.data["ticket"]), user)

    def test_signing_in_is_required(self):
        response = self.client.post(reverse("realtime-ticket"))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class TicketCheckTests(TestCase):
    def setUp(self):
        self.user = make_user(User.Role.INTERPRETER)

    def test_an_expired_ticket_is_refused(self):
        ticket = issue_ticket(self.user)
        later = time.time() + TICKET_MAX_AGE_SECONDS + 1

        with patch("django.core.signing.time.time", return_value=later):
            self.assertIsNone(user_from_ticket(ticket))

    def test_a_changed_ticket_is_refused(self):
        self.assertIsNone(user_from_ticket(issue_ticket(self.user) + "x"))

    def test_a_ticket_for_a_deactivated_user_is_refused(self):
        ticket = issue_ticket(self.user)
        User.objects.filter(pk=self.user.pk).update(is_active=False)

        self.assertIsNone(user_from_ticket(ticket))

    def test_other_signed_values_are_not_tickets(self):
        # Same secret key, different purpose: must not work as a ticket.
        self.assertIsNone(user_from_ticket(signing.dumps({"user": self.user.pk})))
