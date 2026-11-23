"""The wallet: balances, mobile money top-ups, payouts, the provider and its webhook."""

from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.notifications.models import Notification
from apps.wallet import services
from apps.wallet.models import WalletEntry
from apps.wallet.providers import (
    Charge,
    FlutterwaveProvider,
    ProviderError,
    ProviderNotConfigured,
    Verification,
)

User = get_user_model()
HASH = "test-secret-hash"


def make_user(phone, name, role="deaf_hoh"):
    return User.objects.create_user(
        phone_number=phone,
        password="TestPassword123!",
        role=role,
        full_name=name,
        email=f"u{phone[-4:]}@example.com",
    )


def entry(user, amount, status=WalletEntry.Status.COMPLETED, kind=None, ref=None):
    kind = kind or (WalletEntry.Kind.TOPUP if amount > 0 else WalletEntry.Kind.PAYOUT)
    return WalletEntry.objects.create(
        user=user,
        kind=kind,
        status=status,
        amount=amount,
        reference=ref or f"t-{WalletEntry.objects.count()}",
    )


class FakeProvider:
    """Stands in for Flutterwave: nothing here touches the network."""

    name = "flutterwave"
    is_configured = True

    def __init__(self):
        self.charge = Charge("pending", provider_ref="777")
        self.verification = Verification("pending")
        self.charged = []
        self.genuine = True
        self.verify_error = None

    def ensure_ready(self):
        pass

    def charge_mobile_money(self, reference, amount, phone, network, email, full_name):
        self.charged.append((reference, amount, phone, network, email, full_name))
        return self.charge

    def verify(self, reference):
        if self.verify_error:
            raise self.verify_error
        return self.verification

    def webhook_is_genuine(self, header_value):
        return self.genuine and header_value == HASH


class WalletBase(APITestCase):
    def setUp(self):
        self.irene = make_user("+256700000501", "Irene Test")
        self.joy = make_user("+256700000502", "Joy Interpreter", role="interpreter")
        self.provider = FakeProvider()
        patcher = mock.patch(
            "apps.wallet.services.get_provider", return_value=self.provider
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        views_patcher = mock.patch(
            "apps.wallet.views.get_provider", return_value=self.provider
        )
        views_patcher.start()
        self.addCleanup(views_patcher.stop)
        self.client.force_authenticate(self.irene)

    def topup(self, **body):
        data = {"amount": 20000, "network": "MTN"}
        data.update(body)
        return self.client.post(reverse("wallet:topup-create"), data, format="json")

    def payout(self, **body):
        data = {"amount": 10000, "network": "AIRTEL"}
        data.update(body)
        return self.client.post(reverse("wallet:payout-create"), data, format="json")

    def webhook(self, reference, hash_value=HASH):
        headers = {"HTTP_VERIF_HASH": hash_value} if hash_value else {}
        self.client.force_authenticate(None)
        return self.client.post(
            reverse("wallet:flutterwave-webhook"),
            {"event": "charge.completed", "data": {"tx_ref": reference}},
            format="json",
            **headers,
        )


class BalanceTests(TestCase):
    def setUp(self):
        self.user = make_user("+256700000503", "Balance Test")

    def test_a_new_wallet_is_empty(self):
        self.assertEqual(
            services.balance(self.user), {"available": 0, "pending": 0, "earned": 0}
        )

    def test_only_completed_entries_are_spendable(self):
        entry(self.user, 50000)
        entry(self.user, 7000, status=WalletEntry.Status.PENDING)
        entry(self.user, 9000, status=WalletEntry.Status.FAILED)

        self.assertEqual(
            services.balance(self.user),
            {"available": 50000, "pending": 7000, "earned": 50000},
        )

    def test_earned_counts_what_came_in_even_after_it_was_paid_out(self):
        entry(self.user, 50000)
        entry(self.user, 8000)
        entry(self.user, -20000)
        entry(self.user, -5000, status=WalletEntry.Status.PENDING)
        entry(self.user, 4000, status=WalletEntry.Status.PENDING)
        entry(self.user, 9000, status=WalletEntry.Status.FAILED)

        balance = services.balance(self.user)

        self.assertEqual(balance["earned"], 58000)
        self.assertEqual(balance["available"], 33000)

    def test_a_pending_payout_is_held_back_and_a_failed_one_is_not(self):
        entry(self.user, 50000)
        entry(self.user, -20000, status=WalletEntry.Status.PENDING)
        entry(self.user, -5000, status=WalletEntry.Status.FAILED)

        self.assertEqual(services.balance(self.user)["available"], 30000)

    def test_balances_are_separate_per_person(self):
        other = make_user("+256700000504", "Someone Else")
        entry(self.user, 1000)
        entry(other, 5000)

        self.assertEqual(services.balance(self.user)["available"], 1000)


class WalletViewTests(WalletBase):
    def test_shows_the_balance_and_recent_entries(self):
        entry(self.irene, 40000)
        entry(self.irene, 5000, status=WalletEntry.Status.PENDING)

        data = self.client.get(reverse("wallet:wallet")).data

        self.assertEqual(data["currency"], "UGX")
        self.assertEqual((data["available"], data["pending"]), (40000, 5000))
        self.assertEqual(data["earned"], 40000)
        self.assertTrue(data["payments_available"])
        self.assertEqual(len(data["transactions"]), 2)

    def test_says_when_payments_are_switched_off(self):
        self.provider.is_configured = False

        self.assertFalse(
            self.client.get(reverse("wallet:wallet")).data["payments_available"]
        )

    def test_phone_numbers_are_shown_as_last_four_digits_only(self):
        WalletEntry.objects.create(
            user=self.irene,
            kind=WalletEntry.Kind.TOPUP,
            amount=1000,
            reference="x1",
            phone_number="256772123456",
            network="MTN",
        )

        row = self.client.get(reverse("wallet:wallet")).data["transactions"][0]

        self.assertEqual(row["phone_ending"], "3456")
        self.assertNotIn("256772123456", str(row))

    def test_you_only_see_your_own_entries(self):
        entry(self.joy, 99000)

        self.assertEqual(self.client.get(reverse("wallet:wallet")).data["available"], 0)

    def test_institutions_and_signed_out_people_have_no_wallet(self):
        institution = make_user("+256700000505", "Org", role="institution")
        self.client.force_authenticate(institution)
        self.assertEqual(self.client.get(reverse("wallet:wallet")).status_code, 403)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(reverse("wallet:wallet")).status_code, 401)


class TopUpTests(WalletBase):
    def test_starting_a_top_up_asks_the_phone_and_waits(self):
        response = self.topup()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], "pending")
        self.assertEqual(response.data["amount"], 20000)
        reference, amount, phone, network, email, name = self.provider.charged[0]
        self.assertEqual(
            (amount, phone, network, name),
            (20000, "256700000501", "MTN", "Irene Test"),
        )
        self.assertEqual(reference, response.data["reference"])
        self.assertEqual(services.balance(self.irene)["available"], 0)

    def test_a_pending_top_up_shows_as_incoming_not_spendable(self):
        self.topup()

        self.assertEqual(
            services.balance(self.irene),
            {"available": 0, "pending": 20000, "earned": 0},
        )

    def test_another_number_can_be_used(self):
        self.topup(phone_number="0772123456", network="AIRTEL")

        self.assertEqual(self.provider.charged[0][2:4], ("256772123456", "AIRTEL"))

    def test_bad_requests_are_refused_and_nothing_is_sent(self):
        for body in (
            {"amount": 10},  # below the minimum
            {"amount": 5_000_000},  # above the ceiling
            {"amount": 20000, "network": "ORANGE"},
            {"phone_number": "12"},
        ):
            self.assertEqual(self.topup(**body).status_code, 400, body)

        self.assertEqual(self.provider.charged, [])
        self.assertEqual(WalletEntry.objects.count(), 0)

    def test_a_refused_charge_is_recorded_as_failed(self):
        self.provider.charge = Charge("failed", message="Insufficient funds on phone")

        response = self.topup()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], "failed")
        self.assertIn("Insufficient", response.data["failure_reason"])
        self.assertEqual(
            services.balance(self.irene), {"available": 0, "pending": 0, "earned": 0}
        )

    def test_when_payments_are_off_nothing_is_saved(self):
        self.provider.ensure_ready = mock.Mock(side_effect=ProviderNotConfigured())

        response = self.topup()

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(WalletEntry.objects.count(), 0)

    def test_when_the_provider_is_down_the_attempt_is_failed(self):
        self.provider.charge_mobile_money = mock.Mock(side_effect=ProviderError("down"))

        response = self.topup()

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(WalletEntry.objects.get().status, WalletEntry.Status.FAILED)

    def test_only_deaf_users_can_top_up(self):
        self.client.force_authenticate(self.joy)

        self.assertEqual(self.topup().status_code, 403)


class SettleTests(WalletBase):
    def start(self, amount=20000):
        return WalletEntry.objects.get(
            reference=self.topup(amount=amount).data["reference"]
        )

    def paid(self, amount=20000, currency="UGX", ref="FLW-1"):
        self.provider.verification = Verification(
            "successful", Decimal(amount), currency, ref
        )

    def poll(self, reference):
        return self.client.get(reverse("wallet:topup-detail", args=[reference]))

    def test_checking_a_paid_top_up_credits_the_wallet(self):
        started = self.start()
        self.paid()

        response = self.poll(started.reference)

        self.assertEqual(response.data["status"], "completed")
        self.assertEqual(
            services.balance(self.irene),
            {"available": 20000, "pending": 0, "earned": 20000},
        )
        self.assertEqual(WalletEntry.objects.get().provider_ref, "FLW-1")

    def test_checking_one_still_waiting_changes_nothing(self):
        started = self.start()

        self.assertEqual(self.poll(started.reference).data["status"], "pending")

    def test_a_declined_payment_fails(self):
        started = self.start()
        self.provider.verification = Verification("failed")

        self.assertEqual(self.poll(started.reference).data["status"], "failed")
        self.assertEqual(
            services.balance(self.irene), {"available": 0, "pending": 0, "earned": 0}
        )

    def test_checking_twice_credits_once(self):
        started = self.start()
        self.paid()

        self.poll(started.reference)
        self.poll(started.reference)

        self.assertEqual(services.balance(self.irene)["available"], 20000)
        self.assertEqual(Notification.objects.filter(user=self.irene).count(), 1)

    def test_a_payment_of_the_wrong_amount_or_currency_is_not_credited(self):
        started = self.start()
        self.paid(amount=100)

        self.assertEqual(self.poll(started.reference).data["status"], "failed")
        wrong_currency = self.start()
        self.paid(currency="USD")
        self.assertEqual(self.poll(wrong_currency.reference).data["status"], "failed")
        self.assertEqual(services.balance(self.irene)["available"], 0)

    def test_paying_more_than_asked_credits_only_what_was_asked(self):
        started = self.start()
        self.paid(amount=25000)

        self.poll(started.reference)

        self.assertEqual(services.balance(self.irene)["available"], 20000)

    def test_a_provider_outage_while_checking_leaves_it_waiting(self):
        started = self.start()
        self.provider.verify_error = ProviderError("down")

        response = self.poll(started.reference)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "pending")

    def test_you_cannot_check_someone_elses_top_up(self):
        started = self.start()
        self.client.force_authenticate(make_user("+256700000506", "Other Deaf"))

        self.assertEqual(self.poll(started.reference).status_code, 404)

    def test_a_top_up_is_announced_to_the_person(self):
        started = self.start()
        self.paid()
        self.poll(started.reference)

        note = Notification.objects.get(user=self.irene)
        self.assertEqual(note.title, "Top-up received")
        self.assertIn("20,000", note.body)


class WebhookTests(WalletBase):
    def setUp(self):
        super().setUp()
        self.reference = self.topup().data["reference"]

    def test_a_genuine_webhook_credits_the_wallet(self):
        self.provider.verification = Verification(
            "successful", Decimal(20000), "UGX", "9"
        )

        response = self.webhook(self.reference)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(services.balance(self.irene)["available"], 20000)

    def test_the_same_webhook_twice_pays_once(self):
        self.provider.verification = Verification(
            "successful", Decimal(20000), "UGX", "9"
        )

        self.webhook(self.reference)
        self.webhook(self.reference)

        self.assertEqual(services.balance(self.irene)["available"], 20000)

    def test_a_wrong_or_missing_hash_is_refused_and_nothing_happens(self):
        self.provider.verification = Verification(
            "successful", Decimal(20000), "UGX", "9"
        )

        self.assertEqual(
            self.webhook(self.reference, hash_value="wrong").status_code, 401
        )
        self.assertEqual(self.webhook(self.reference, hash_value=None).status_code, 401)
        self.assertEqual(services.balance(self.irene)["available"], 0)

    def test_we_check_with_the_provider_not_the_webhook_body(self):
        # The body claims success, but the provider says it is still waiting.
        self.client.force_authenticate(None)
        response = self.client.post(
            reverse("wallet:flutterwave-webhook"),
            {
                "event": "charge.completed",
                "data": {"tx_ref": self.reference, "status": "successful"},
            },
            format="json",
            HTTP_VERIF_HASH=HASH,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(services.balance(self.irene)["available"], 0)

    def test_an_unknown_reference_is_acknowledged_and_ignored(self):
        self.assertEqual(self.webhook("someone-elses-ref").status_code, 200)

    def test_a_provider_outage_asks_for_a_retry(self):
        self.provider.verify_error = ProviderError("down")

        self.assertEqual(self.webhook(self.reference).status_code, 502)

    def test_garbage_bodies_are_harmless(self):
        self.client.force_authenticate(None)
        for body in ([], {"data": "x"}, {"data": {}}):
            response = self.client.post(
                reverse("wallet:flutterwave-webhook"),
                body,
                format="json",
                HTTP_VERIF_HASH=HASH,
            )
            self.assertEqual(response.status_code, 200)


class PayoutTests(WalletBase):
    def setUp(self):
        super().setUp()
        entry(self.joy, 50000, kind=WalletEntry.Kind.SESSION_EARNING)
        self.client.force_authenticate(self.joy)

    def test_a_payout_is_held_back_straight_away(self):
        response = self.payout(amount=30000)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], "pending")
        self.assertEqual(response.data["amount"], -30000)
        self.assertEqual(services.balance(self.joy)["available"], 20000)
        self.assertEqual(
            WalletEntry.objects.get(reference=response.data["reference"]).network,
            "AIRTEL",
        )

    def test_you_cannot_pay_out_more_than_you_have(self):
        self.assertEqual(self.payout(amount=60000).status_code, 400)
        self.payout(amount=40000)
        self.assertEqual(self.payout(amount=20000).status_code, 400)  # only 10,000 left

        self.assertEqual(services.balance(self.joy)["available"], 10000)

    def test_only_interpreters_can_ask_for_a_payout(self):
        self.client.force_authenticate(self.irene)

        self.assertEqual(self.payout().status_code, 403)

    def test_the_smallest_amount_is_enforced(self):
        self.assertEqual(self.payout(amount=100).status_code, 400)

    def test_marking_it_paid_completes_it_and_tells_the_interpreter(self):
        payout = WalletEntry.objects.get(
            reference=self.payout(amount=30000).data["reference"]
        )

        services.complete_payout(payout.pk, provider_ref="BANK-1")

        payout.refresh_from_db()
        self.assertEqual(payout.status, WalletEntry.Status.COMPLETED)
        self.assertEqual(services.balance(self.joy)["available"], 20000)
        self.assertEqual(Notification.objects.get(user=self.joy).title, "Payout sent")

    def test_rejecting_it_gives_the_money_back(self):
        payout = WalletEntry.objects.get(
            reference=self.payout(amount=30000).data["reference"]
        )

        services.reject_payout(payout.pk, "Wrong number")

        self.assertEqual(services.balance(self.joy)["available"], 50000)
        self.assertEqual(
            Notification.objects.get(user=self.joy).title, "Payout not sent"
        )

    def test_a_settled_payout_cannot_be_settled_again(self):
        payout = WalletEntry.objects.get(
            reference=self.payout(amount=30000).data["reference"]
        )
        services.complete_payout(payout.pk)

        self.assertIsNone(services.reject_payout(payout.pk))
        self.assertIsNone(services.complete_payout(payout.pk))
        self.assertEqual(services.balance(self.joy)["available"], 20000)


class LedgerRuleTests(TestCase):
    def test_a_zero_entry_is_refused(self):
        from django.db import IntegrityError, transaction

        user = make_user("+256700000507", "Zero Test")
        with self.assertRaises(IntegrityError), transaction.atomic():
            entry(user, 0, kind=WalletEntry.Kind.ADJUSTMENT)


class FlutterwaveProviderTests(SimpleTestCase):
    """The HTTP calls, with the network replaced."""

    def provider(self, key="FLWSECK_TEST-abc"):
        return FlutterwaveProvider(secret_key=key, base_url="https://example.test/v3")

    def reply(self, body, status_code=200):
        response = mock.Mock(status_code=status_code)
        response.json.return_value = body
        return response

    def test_no_key_means_payments_are_off(self):
        provider = self.provider(key="")

        self.assertFalse(provider.is_configured)
        with self.assertRaises(ProviderNotConfigured):
            provider.ensure_ready()
        with self.assertRaises(ProviderNotConfigured):
            provider.verify("ref")

    def test_the_charge_goes_to_the_uganda_mobile_money_endpoint_with_the_key(self):
        with mock.patch(
            "apps.wallet.providers.requests.request",
            return_value=self.reply({"status": "success", "data": {"id": 123}}),
        ) as call:
            result = self.provider().charge_mobile_money(
                "topup-1", 20000, "256772123456", "MTN", "a@b.co", "Irene"
            )

        method, url = call.call_args.args
        self.assertEqual((method, url), ("POST", "https://example.test/v3/charges"))
        self.assertEqual(
            call.call_args.kwargs["params"], {"type": "mobile_money_uganda"}
        )
        self.assertEqual(
            call.call_args.kwargs["headers"],
            {"Authorization": "Bearer FLWSECK_TEST-abc"},
        )
        self.assertEqual(
            call.call_args.kwargs["json"],
            {
                "tx_ref": "topup-1",
                "amount": 20000,
                "currency": "UGX",
                "email": "a@b.co",
                "phone_number": "256772123456",
                "network": "MTN",
                "fullname": "Irene",
            },
        )
        self.assertEqual(result, Charge("pending", provider_ref="123"))

    def test_a_refused_charge_carries_the_providers_message(self):
        with mock.patch(
            "apps.wallet.providers.requests.request",
            return_value=self.reply(
                {"status": "error", "message": "Invalid phone"}, 400
            ),
        ):
            result = self.provider().charge_mobile_money(
                "r", 1000, "1", "MTN", "a@b.co", "N"
            )

        self.assertEqual((result.status, result.message), ("failed", "Invalid phone"))

    def test_verifying_reads_status_amount_and_currency(self):
        body = {
            "status": "success",
            "data": {
                "id": 5,
                "status": "successful",
                "amount": 20000,
                "currency": "UGX",
            },
        }
        with mock.patch(
            "apps.wallet.providers.requests.request", return_value=self.reply(body)
        ) as call:
            result = self.provider().verify("topup-1")

        self.assertEqual(call.call_args.kwargs["params"], {"tx_ref": "topup-1"})
        self.assertEqual(result, Verification("successful", Decimal(20000), "UGX", "5"))

    def test_an_unknown_payment_counts_as_still_waiting(self):
        with mock.patch(
            "apps.wallet.providers.requests.request",
            return_value=self.reply(
                {"status": "error", "message": "No transaction"}, 404
            ),
        ):
            self.assertEqual(self.provider().verify("x").status, "pending")

    def test_odd_statuses_are_treated_as_waiting_not_paid(self):
        body = {
            "status": "success",
            "data": {"id": 5, "status": "weird", "amount": 1, "currency": "UGX"},
        }
        with mock.patch(
            "apps.wallet.providers.requests.request", return_value=self.reply(body)
        ):
            self.assertEqual(self.provider().verify("x").status, "pending")

    def test_network_trouble_and_bad_replies_become_provider_errors(self):
        import requests

        provider = self.provider()
        with mock.patch(
            "apps.wallet.providers.requests.request",
            side_effect=requests.Timeout("slow"),
        ):
            with self.assertRaises(ProviderError):
                provider.verify("x")
        broken = mock.Mock(status_code=200)
        broken.json.side_effect = ValueError("not json")
        with mock.patch("apps.wallet.providers.requests.request", return_value=broken):
            with self.assertRaises(ProviderError):
                provider.verify("x")
        with mock.patch(
            "apps.wallet.providers.requests.request", return_value=self.reply([1])
        ):
            with self.assertRaises(ProviderError):
                provider.verify("x")

    @override_settings(FLUTTERWAVE_WEBHOOK_HASH=HASH)
    def test_the_webhook_hash_must_match_exactly(self):
        provider = self.provider()

        self.assertTrue(provider.webhook_is_genuine(HASH))
        self.assertFalse(provider.webhook_is_genuine("nope"))
        self.assertFalse(provider.webhook_is_genuine(""))
        self.assertFalse(provider.webhook_is_genuine(None))

    @override_settings(FLUTTERWAVE_WEBHOOK_HASH="")
    def test_with_no_hash_set_no_webhook_is_trusted(self):
        self.assertFalse(self.provider().webhook_is_genuine(""))
        self.assertFalse(self.provider().webhook_is_genuine("anything"))
