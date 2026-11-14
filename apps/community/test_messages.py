"""Connected Deaf users messaging each other."""

from django.urls import reverse
from rest_framework import status

from apps.community.models import Connection, Message
from apps.community.test_connections import CommunityBase, make_user


class MessagingBase(CommunityBase):
    def setUp(self):
        super().setUp()
        self.link = Connection.objects.create(
            requester=self.me, addressee=self.sarah, status=Connection.Status.ACCEPTED
        )
        # A connection that was only asked for, with no chat yet.
        self.pending = Connection.objects.create(
            requester=self.me, addressee=self.daniel
        )

    def url(self, name, pk=None):
        return reverse(f"community:{name}", args=[pk or self.link.id])

    def send(self, body, user=None):
        if user:
            self.client.force_authenticate(user)
        return self.client.post(self.url("messages"), {"body": body}, format="json")

    def messages(self, **params):
        return self.client.get(self.url("messages"), params).data


class AccessTests(MessagingBase):
    def test_needs_a_signed_in_deaf_user(self):
        self.client.force_authenticate(None)
        self.assertEqual(
            self.client.get(reverse("community:conversations")).status_code,
            status.HTTP_401_UNAUTHORIZED,
        )
        self.client.force_authenticate(
            make_user("+256700000420", "An Interpreter", role="interpreter")
        )
        self.assertEqual(
            self.client.get(self.url("messages")).status_code, status.HTTP_403_FORBIDDEN
        )

    def test_strangers_cannot_read_or_write(self):
        self.client.force_authenticate(self.daniel)

        self.assertEqual(
            self.client.get(self.url("messages")).status_code, status.HTTP_404_NOT_FOUND
        )
        self.assertEqual(self.send("hi").status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            self.client.post(self.url("conversation-read")).status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(Message.objects.count(), 0)

    def test_a_request_that_is_not_accepted_is_not_a_conversation(self):
        self.assertEqual(
            self.client.get(self.url("messages", self.pending.id)).status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(
            self.client.post(
                self.url("messages", self.pending.id), {"body": "hi"}, format="json"
            ).status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_removing_the_connection_removes_the_chat(self):
        self.send("hello")

        self.client.delete(reverse("community:connection-detail", args=[self.link.id]))

        self.assertEqual(Message.objects.count(), 0)
        self.assertEqual(
            self.client.get(self.url("messages")).status_code, status.HTTP_404_NOT_FOUND
        )


class SendTests(MessagingBase):
    def test_a_message_is_saved_and_returned(self):
        response = self.send("  Hi Sarah  ")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["body"], "Hi Sarah")
        self.assertTrue(response.data["sent_by_me"])
        self.assertFalse(response.data["read"])
        self.assertEqual(Message.objects.get().sender, self.me)

    def test_empty_and_too_long_messages_are_refused(self):
        for body in ("", "   ", "x" * 2001):
            self.assertEqual(
                self.send(body).status_code, status.HTTP_400_BAD_REQUEST, body[:5]
            )
        self.assertEqual(self.send("x" * 2000).status_code, status.HTTP_201_CREATED)
        self.assertEqual(Message.objects.count(), 1)

    def test_both_people_see_the_same_conversation(self):
        self.send("Hi Sarah")
        self.send("Hi Irene", user=self.sarah)

        self.client.force_authenticate(self.me)
        mine = self.messages()["messages"]

        self.assertEqual([m["body"] for m in mine], ["Hi Sarah", "Hi Irene"])
        self.assertEqual([m["sent_by_me"] for m in mine], [True, False])


class ReadAndUnreadTests(MessagingBase):
    def conversation(self):
        return self.client.get(reverse("community:conversations")).data[0]

    def test_unread_counts_only_what_the_other_person_sent(self):
        self.send("one")
        self.send("two")
        self.send("mine", user=self.sarah)

        # The last send was as Sarah, so the client is Sarah: she has Irene's two unread.
        self.assertEqual(self.conversation()["unread_count"], 2)
        self.client.force_authenticate(self.me)
        self.assertEqual(self.conversation()["unread_count"], 1)

    def test_opening_the_conversation_clears_unread_and_shows_ticks(self):
        self.send("hello")
        self.client.force_authenticate(self.sarah)
        self.assertEqual(self.conversation()["unread_count"], 1)

        response = self.client.post(self.url("conversation-read"))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(self.conversation()["unread_count"], 0)
        self.client.force_authenticate(self.me)
        self.assertTrue(self.messages()["messages"][0]["read"])

    def test_reading_does_not_mark_your_own_messages_read(self):
        self.send("hello")

        self.client.post(self.url("conversation-read"))

        self.assertFalse(self.messages()["messages"][0]["read"])


class ConversationListTests(MessagingBase):
    def test_lists_connections_with_the_latest_message(self):
        self.send("first")
        self.send("second")

        rows = self.client.get(reverse("community:conversations")).data

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], self.link.id)
        self.assertEqual(rows[0]["person"]["name"], "Sarah Nsubuga")
        self.assertEqual(rows[0]["last_message"]["body"], "second")

    def test_a_connection_with_no_messages_still_appears(self):
        rows = self.client.get(reverse("community:conversations")).data

        self.assertIsNone(rows[0]["last_message"])
        self.assertEqual(rows[0]["unread_count"], 0)

    def test_most_recent_activity_comes_first(self):
        other = Connection.objects.create(
            requester=self.daniel, addressee=self.me, status=Connection.Status.ACCEPTED
        )
        self.send("to sarah")
        self.client.post(
            self.url("messages", other.id), {"body": "to daniel"}, format="json"
        )

        names = [
            r["person"]["name"]
            for r in self.client.get(reverse("community:conversations")).data
        ]

        self.assertEqual(names, ["Daniel Ochieng", "Sarah Nsubuga"])

    def test_unaccepted_requests_are_not_conversations(self):
        names = [
            r["person"]["name"]
            for r in self.client.get(reverse("community:conversations")).data
        ]

        self.assertNotIn("Daniel Ochieng", names)


class PagingTests(MessagingBase):
    def setUp(self):
        super().setUp()
        for n in range(1, 8):
            Message.objects.create(connection=self.link, sender=self.me, body=f"m{n}")

    def test_returns_the_newest_page_oldest_first(self):
        page = self.messages(limit=3)

        self.assertEqual([m["body"] for m in page["messages"]], ["m5", "m6", "m7"])
        self.assertTrue(page["has_more"])

    def test_before_walks_back_to_older_messages(self):
        first = self.messages(limit=3)
        older = self.messages(limit=3, before=first["messages"][0]["id"])

        self.assertEqual([m["body"] for m in older["messages"]], ["m2", "m3", "m4"])
        last = self.messages(limit=3, before=older["messages"][0]["id"])
        self.assertEqual([m["body"] for m in last["messages"]], ["m1"])
        self.assertFalse(last["has_more"])

    def test_bad_numbers_are_refused(self):
        response = self.client.get(self.url("messages"), {"limit": "lots"})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
