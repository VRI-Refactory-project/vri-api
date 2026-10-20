"""V2-010: an interpreter accepts or declines an offer, and the offers screen gets what it reads."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.audit.models import AuditLog
from apps.institutions.models import InstitutionProfile, InstitutionStaff, InstitutionStatus, StaffRole
from apps.interpreters.models import Specialisation
from apps.service_requests.models import RequestOffer, ServiceRequest
from apps.service_requests.services import create_request, sweep
from apps.service_requests.test_offers import make_interpreter, make_user

User = get_user_model()
Status = ServiceRequest.Status
Outcome = RequestOffer.Outcome


class OfferResponseTests(APITestCase):
    def setUp(self):
        self.interpreter = make_interpreter()
        self.deaf = make_user(User.Role.DEAF_HOH)
        self.service_request, _ = create_request(requester=self.deaf)
        self.offer = self.service_request.offers.get()
        self.client.force_authenticate(self.interpreter.user)

    def respond(self, action, offer=None):
        url = reverse("interpreter-offer-respond", kwargs={"pk": (offer or self.offer).pk})
        return self.client.post(url, {"action": action}, format="json")

    def state(self, offer=None):
        return self.client.get(reverse("interpreter-offer-state", kwargs={"pk": (offer or self.offer).pk}))

    def expire_offer(self):
        RequestOffer.objects.filter(pk=self.offer.pk).update(expires_at=timezone.now() - timedelta(seconds=1))

    # --- The offers list (what the screen shows) ---

    def test_the_list_has_exactly_what_the_screen_reads(self):
        response = self.client.get(reverse("interpreter-offer-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        item = response.data[0]
        self.assertEqual(
            set(item), {"id", "title", "institutionName", "sector", "language", "participants", "expiresAt"}
        )
        self.assertEqual(item["id"], str(self.offer.pk))
        self.assertEqual(item["title"], "General interpreting request")
        self.assertEqual(item["institutionName"], self.deaf.full_name)
        self.assertEqual(item["sector"], "General")
        self.assertEqual(item["language"], "Uganda Sign Language ⇄ English")
        self.assertEqual(item["participants"], "Deaf user and hearing participant")
        self.assertIsNotNone(item["expiresAt"])

    def test_an_emergency_says_so_in_the_title(self):
        other = make_interpreter(Specialisation.MEDICAL)
        create_request(
            requester=make_user(User.Role.DEAF_HOH),
            specialisation_required=Specialisation.MEDICAL,
            priority=ServiceRequest.Priority.EMERGENCY,
        )
        self.client.force_authenticate(other.user)

        item = self.client.get(reverse("interpreter-offer-list")).data[0]

        self.assertEqual(item["title"], "Emergency: Medical interpreting request")
        self.assertEqual(item["sector"], "Medical")

    def test_an_institution_request_shows_the_institution_name(self):
        staff_user = make_user(User.Role.INSTITUTION)
        institution = InstitutionProfile.objects.create(
            user=staff_user,
            organisation_name="Mulago Hospital",
            organisation_type="HOSPITAL",
            status=InstitutionStatus.APPROVED,
        )
        InstitutionStaff.objects.create(institution=institution, user=staff_user, role=StaffRole.ADMIN)
        other = make_interpreter()
        create_request(requester=staff_user)
        self.client.force_authenticate(other.user)

        item = self.client.get(reverse("interpreter-offer-list")).data[0]

        self.assertEqual(item["institutionName"], "Mulago Hospital")

    def test_an_expired_offer_is_not_listed(self):
        self.expire_offer()

        self.assertEqual(self.client.get(reverse("interpreter-offer-list")).data, [])

    def test_only_the_interpreters_own_offers_are_listed(self):
        self.client.force_authenticate(make_interpreter().user)

        self.assertEqual(self.client.get(reverse("interpreter-offer-list")).data, [])

    def test_only_interpreters_can_use_the_offers_screen(self):
        self.client.force_authenticate(self.deaf)

        self.assertEqual(self.client.get(reverse("interpreter-offer-list")).status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.respond("accept").status_code, status.HTTP_403_FORBIDDEN)

    # --- Accepting (BE-01, BE-02) ---

    def test_accepting_assigns_the_request(self):
        response = self.respond("accept")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"status": "accepted"})
        self.service_request.refresh_from_db()
        self.offer.refresh_from_db()
        self.assertEqual(self.service_request.status, Status.ASSIGNED)
        self.assertEqual(self.service_request.interpreter, self.interpreter)
        self.assertIsNotNone(self.service_request.assigned_at)
        self.assertEqual(self.offer.outcome, Outcome.ACCEPTED)
        self.assertIsNotNone(self.offer.responded_at)

    def test_the_requester_sees_who_accepted(self):
        self.respond("accept")
        self.client.force_authenticate(self.deaf)

        data = self.client.get(reverse("service-request-detail", kwargs={"pk": self.service_request.pk})).data

        self.assertEqual(data["status"], Status.ASSIGNED)
        self.assertEqual(data["interpreter"]["name"], self.interpreter.user.full_name)

    def test_accepting_switches_the_interpreter_to_unavailable(self):
        self.respond("accept")

        self.interpreter.refresh_from_db()
        self.assertFalse(self.interpreter.is_available)

    def test_an_interpreter_in_a_session_is_not_offered_another_request(self):
        self.respond("accept")

        next_request, _ = create_request(requester=make_user(User.Role.DEAF_HOH))

        self.assertEqual(next_request.status, Status.PENDING)

    def test_accepting_is_audited(self):
        self.respond("accept")

        entry = AuditLog.objects.get(event=AuditLog.Event.OFFER_ACCEPTED)
        self.assertEqual(entry.user, self.interpreter.user)
        self.assertEqual(entry.target_user, self.deaf)
        self.assertEqual(entry.details, {"request_id": self.service_request.pk, "offer_id": self.offer.pk})

    def test_accepting_twice_is_fine(self):
        self.respond("accept")

        response = self.respond("accept")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(AuditLog.objects.filter(event=AuditLog.Event.OFFER_ACCEPTED).count(), 1)

    # --- Refusing offers that are no longer active (BE-03) ---

    def test_an_expired_offer_cannot_be_accepted(self):
        self.expire_offer()

        response = self.respond("accept")

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["detail"], "This offer has expired.")
        self.service_request.refresh_from_db()
        self.assertNotEqual(self.service_request.status, Status.ASSIGNED)

    def test_a_cancelled_request_cannot_be_accepted(self):
        self.client.force_authenticate(self.deaf)
        self.client.post(reverse("service-request-cancel", kwargs={"pk": self.service_request.pk}))
        self.client.force_authenticate(self.interpreter.user)

        response = self.respond("accept")

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["detail"], "This request was cancelled.")

    def test_changing_your_answer_is_refused(self):
        self.respond("accept")

        response = self.respond("decline")

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["detail"], "You have already accepted this offer.")
        self.service_request.refresh_from_db()
        self.assertEqual(self.service_request.status, Status.ASSIGNED)

    def test_someone_elses_offer_is_not_found(self):
        self.client.force_authenticate(make_interpreter().user)

        self.assertEqual(self.respond("accept").status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.state().status_code, status.HTTP_404_NOT_FOUND)

    def test_an_unknown_action_is_rejected(self):
        self.assertEqual(self.respond("maybe").status_code, status.HTTP_400_BAD_REQUEST)

    # --- Declining (BE-04, BE-06) ---

    def test_declining_goes_straight_to_the_next_interpreter(self):
        second = make_interpreter()

        response = self.respond("decline")

        self.assertEqual(response.data, {"status": "declined"})
        self.service_request.refresh_from_db()
        self.assertEqual(self.service_request.status, Status.OFFERED)
        self.assertEqual(self.service_request.interpreter, second)
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.outcome, Outcome.DECLINED)
        self.assertIsNotNone(self.offer.responded_at)

    def test_declining_with_nobody_else_free_puts_the_request_back_in_the_queue(self):
        self.respond("decline")

        self.service_request.refresh_from_db()
        self.assertEqual(self.service_request.status, Status.PENDING)
        self.assertIsNone(self.service_request.interpreter)

    def test_whoever_declined_is_never_asked_again(self):
        self.respond("decline")

        sweep()

        self.assertEqual(self.service_request.offers.filter(interpreter=self.interpreter).count(), 1)

    def test_declining_keeps_the_interpreter_available(self):
        self.respond("decline")

        self.interpreter.refresh_from_db()
        self.assertTrue(self.interpreter.is_available)

    def test_declining_is_audited(self):
        self.respond("decline")

        entry = AuditLog.objects.get(event=AuditLog.Event.OFFER_DECLINED)
        self.assertEqual(entry.user, self.interpreter.user)
        self.assertEqual(entry.details["offer_id"], self.offer.pk)

    # --- Expiry and the state the screen polls (BE-05, BE-07) ---

    def test_state_is_pending_while_waiting(self):
        self.assertEqual(self.state().data, {"status": "pending"})

    def test_state_after_expiry_is_expired_and_recorded_separately(self):
        self.expire_offer()

        self.assertEqual(self.state().data, {"status": "expired"})
        self.offer.refresh_from_db()
        self.assertEqual(self.offer.outcome, Outcome.EXPIRED)
        self.assertIsNotNone(self.offer.responded_at)

    def test_state_after_the_requester_cancelled_is_rerouted(self):
        self.client.force_authenticate(self.deaf)
        self.client.post(reverse("service-request-cancel", kwargs={"pk": self.service_request.pk}))
        self.client.force_authenticate(self.interpreter.user)

        self.assertEqual(self.state().data, {"status": "rerouted"})

    def test_expiry_is_audited(self):
        self.expire_offer()

        sweep()

        entry = AuditLog.objects.get(event=AuditLog.Event.OFFER_EXPIRED)
        self.assertIsNone(entry.user)
        self.assertEqual(entry.target_user, self.interpreter.user)
        self.assertEqual(entry.details, {"request_id": self.service_request.pk, "offer_id": self.offer.pk})
