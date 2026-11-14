"""Deaf users finding each other and connecting."""

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.community.models import Connection
from apps.users.models import UserProfile

User = get_user_model()


def make_user(phone, name, role="deaf_hoh", **extra):
    return User.objects.create_user(
        phone_number=phone,
        password="TestPassword123!",
        role=role,
        full_name=name,
        **extra,
    )


class CommunityBase(APITestCase):
    def setUp(self):
        self.me = make_user("+256700000401", "Irene Test")
        self.sarah = make_user("+256700000402", "Sarah Nsubuga")
        self.daniel = make_user("+256700000403", "Daniel Ochieng")
        self.client.force_authenticate(self.me)

    def ask(self, user):
        return self.client.post(
            reverse("community:requests"), {"user": user.id}, format="json"
        )

    def people(self, **params):
        return self.client.get(reverse("community:people"), params).data

    def relationship(self, user):
        return {p["id"]: p["relationship"] for p in self.people()}[user.id]


class AccessTests(CommunityBase):
    def test_everything_needs_a_signed_in_deaf_user(self):
        urls = [
            reverse("community:people"),
            reverse("community:requests"),
            reverse("community:connections"),
        ]
        self.client.force_authenticate(None)
        for url in urls:
            self.assertEqual(
                self.client.get(url).status_code, status.HTTP_401_UNAUTHORIZED, url
            )
        interpreter = make_user("+256700000410", "An Interpreter", role="interpreter")
        self.client.force_authenticate(interpreter)
        for url in urls:
            self.assertEqual(
                self.client.get(url).status_code, status.HTTP_403_FORBIDDEN, url
            )


class PeopleTests(CommunityBase):
    def test_lists_other_active_deaf_users_only(self):
        make_user("+256700000411", "An Interpreter", role="interpreter")
        make_user("+256700000412", "Gone Away", is_active=False)

        names = [p["name"] for p in self.people()]

        self.assertEqual(names, ["Daniel Ochieng", "Sarah Nsubuga"])

    def test_shows_name_place_and_picture_but_no_contact_details(self):
        UserProfile.objects.create(user=self.sarah, location="Kampala")

        row = next(p for p in self.people() if p["id"] == self.sarah.id)

        self.assertEqual(
            set(row), {"id", "name", "location", "photo_url", "relationship"}
        )
        self.assertEqual(row["location"], "Kampala")
        self.assertIsNone(row["photo_url"])

    def test_search_matches_a_name_or_a_place(self):
        UserProfile.objects.create(user=self.daniel, location="Gulu")

        self.assertEqual(
            [p["name"] for p in self.people(search="sar")], ["Sarah Nsubuga"]
        )
        self.assertEqual(
            [p["name"] for p in self.people(search="gulu")], ["Daniel Ochieng"]
        )
        self.assertEqual(self.people(search="nobody"), [])

    def test_people_start_with_no_relationship(self):
        self.assertEqual(self.relationship(self.sarah), "none")


class SendRequestTests(CommunityBase):
    def test_a_request_is_sent_and_shown_as_sent(self):
        response = self.ask(self.sarah)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["relationship"], "request_sent")
        self.assertEqual(self.relationship(self.sarah), "request_sent")

    def test_the_other_person_sees_it_waiting(self):
        self.ask(self.sarah)
        self.client.force_authenticate(self.sarah)

        incoming = self.client.get(reverse("community:requests")).data

        self.assertEqual([r["person"]["name"] for r in incoming], ["Irene Test"])
        self.assertEqual(self.relationship(self.me), "request_received")

    def test_outgoing_lists_what_you_sent(self):
        self.ask(self.sarah)

        outgoing = self.client.get(
            reverse("community:requests"), {"direction": "outgoing"}
        ).data

        self.assertEqual([r["person"]["name"] for r in outgoing], ["Sarah Nsubuga"])
        self.assertEqual(self.client.get(reverse("community:requests")).data, [])

    def test_a_bad_direction_is_refused(self):
        response = self.client.get(
            reverse("community:requests"), {"direction": "sideways"}
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_ask_yourself(self):
        response = self.ask(self.me)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_ask_the_same_person_twice(self):
        self.ask(self.sarah)

        response = self.ask(self.sarah)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Connection.objects.count(), 1)

    def test_only_deaf_users_can_be_asked(self):
        interpreter = make_user("+256700000413", "An Interpreter", role="interpreter")
        gone = make_user("+256700000414", "Gone Away", is_active=False)

        self.assertEqual(self.ask(interpreter).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.ask(gone).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.ask(self.me).status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            self.client.post(
                reverse("community:requests"), {"user": 99999}, format="json"
            ).status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_asking_someone_who_asked_you_connects_you_straight_away(self):
        self.client.force_authenticate(self.sarah)
        self.ask(self.me)
        self.client.force_authenticate(self.me)

        response = self.ask(self.sarah)

        self.assertEqual(response.data["relationship"], "connected")
        self.assertEqual(Connection.objects.get().status, Connection.Status.ACCEPTED)


class AnswerTests(CommunityBase):
    def setUp(self):
        super().setUp()
        self.ask(self.sarah)
        self.request_id = Connection.objects.get().id
        self.client.force_authenticate(self.sarah)

    def answer(self, name):
        return self.client.post(
            reverse(f"community:request-{name}", args=[self.request_id])
        )

    def test_accepting_connects_both_people(self):
        self.assertEqual(self.answer("accept").status_code, status.HTTP_204_NO_CONTENT)

        self.assertEqual(self.relationship(self.me), "connected")
        self.client.force_authenticate(self.me)
        self.assertEqual(self.relationship(self.sarah), "connected")
        listed = self.client.get(reverse("community:connections")).data
        self.assertEqual([c["person"]["name"] for c in listed], ["Sarah Nsubuga"])

    def test_declining_leaves_the_asker_seeing_a_sent_request(self):
        self.answer("decline")

        self.assertEqual(self.relationship(self.me), "none")
        self.assertEqual(self.client.get(reverse("community:requests")).data, [])
        self.client.force_authenticate(self.me)
        self.assertEqual(self.relationship(self.sarah), "request_sent")
        self.assertEqual(self.client.get(reverse("community:connections")).data, [])

    def test_a_declined_person_cannot_ask_again(self):
        self.answer("decline")
        self.client.force_authenticate(self.me)

        self.assertEqual(self.ask(self.sarah).status_code, status.HTTP_400_BAD_REQUEST)

    def test_the_person_who_declined_can_still_ask_the_other_way(self):
        self.answer("decline")

        response = self.ask(self.me)

        self.assertEqual(response.data["relationship"], "request_sent")
        self.client.force_authenticate(self.me)
        self.assertEqual(self.relationship(self.sarah), "request_received")

    def test_only_the_person_asked_can_answer(self):
        self.client.force_authenticate(self.me)
        self.assertEqual(self.answer("accept").status_code, status.HTTP_404_NOT_FOUND)
        self.client.force_authenticate(self.daniel)
        self.assertEqual(self.answer("decline").status_code, status.HTTP_404_NOT_FOUND)

    def test_an_answered_request_cannot_be_answered_again(self):
        self.answer("accept")

        self.assertEqual(self.answer("decline").status_code, status.HTTP_404_NOT_FOUND)


class CancelAndRemoveTests(CommunityBase):
    def test_the_asker_can_take_a_request_back(self):
        self.ask(self.sarah)
        request_id = Connection.objects.get().id

        response = self.client.delete(
            reverse("community:request-detail", args=[request_id])
        )

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(Connection.objects.count(), 0)
        self.assertEqual(self.relationship(self.sarah), "none")

    def test_the_person_asked_cannot_use_cancel(self):
        self.ask(self.sarah)
        request_id = Connection.objects.get().id
        self.client.force_authenticate(self.sarah)

        response = self.client.delete(
            reverse("community:request-detail", args=[request_id])
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(Connection.objects.count(), 1)

    def test_either_person_can_remove_a_connection(self):
        for remover in (self.me, self.sarah):
            Connection.objects.all().delete()
            connection = Connection.objects.create(
                requester=self.me,
                addressee=self.sarah,
                status=Connection.Status.ACCEPTED,
            )
            self.client.force_authenticate(remover)

            response = self.client.delete(
                reverse("community:connection-detail", args=[connection.id])
            )

            self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
            self.assertEqual(Connection.objects.count(), 0)

    def test_a_stranger_cannot_remove_a_connection(self):
        connection = Connection.objects.create(
            requester=self.me, addressee=self.sarah, status=Connection.Status.ACCEPTED
        )
        self.client.force_authenticate(self.daniel)

        response = self.client.delete(
            reverse("community:connection-detail", args=[connection.id])
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(Connection.objects.count(), 1)
