"""Connected Deaf users messaging each other."""

import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image
from rest_framework import status

from apps.community.models import Connection, Message, MessageImage
from apps.community.test_connections import CommunityBase, make_user
from apps.notifications.models import Notification


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

    def send_sticker(self, sticker):
        return self.client.post(
            self.url("messages"), {"sticker": sticker}, format="json"
        )

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


class StickerTests(MessagingBase):
    def test_a_sticker_is_saved_with_no_text(self):
        response = self.send_sticker("thumbs_up")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["sticker"], "thumbs_up")
        self.assertEqual(response.data["body"], "")
        self.assertEqual(Message.objects.get().sticker, "thumbs_up")

    def test_text_messages_have_no_sticker(self):
        self.assertEqual(self.send("Hi").data["sticker"], "")

    def test_an_unknown_sticker_is_refused(self):
        self.assertEqual(
            self.send_sticker("not_a_sticker").status_code,
            status.HTTP_400_BAD_REQUEST,
        )
        self.assertEqual(Message.objects.count(), 0)

    def test_text_and_a_sticker_together_or_neither_are_refused(self):
        both = self.client.post(
            self.url("messages"), {"body": "Hi", "sticker": "hello"}, format="json"
        )
        neither = self.client.post(self.url("messages"), {}, format="json")

        self.assertEqual(both.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(neither.status_code, status.HTTP_400_BAD_REQUEST)

    def test_the_other_person_sees_the_sticker_and_it_counts_as_unread(self):
        self.send_sticker("heart")

        self.client.force_authenticate(self.sarah)
        shown = self.messages()["messages"]
        row = self.client.get(reverse("community:conversations")).data[0]

        self.assertEqual(shown[0]["sticker"], "heart")
        self.assertEqual(row["unread_count"], 1)
        self.assertEqual(row["last_message"]["sticker"], "heart")

    def test_the_other_person_is_notified_in_words(self):
        self.send_sticker("party")

        notification = Notification.objects.get()
        self.assertEqual(notification.user, self.sarah)
        self.assertEqual(notification.body, "Sent you a sticker")


def picture(width=40, height=30, fmt="PNG", name="shot.png"):
    out = io.BytesIO()
    Image.new("RGB", (width, height), "teal").save(out, format=fmt)
    return SimpleUploadedFile(name, out.getvalue(), content_type=f"image/{fmt.lower()}")


class PhotoTests(MessagingBase):
    def send_photo(self, upload=None, body=None, user=None):
        if user:
            self.client.force_authenticate(user)
        data = {"image": upload or picture()}
        if body is not None:
            data["body"] = body
        return self.client.post(self.url("messages"), data, format="multipart")

    def image_url(self, message_id, conversation=None):
        return reverse(
            "community:message-image",
            args=[conversation or self.link.id, message_id],
        )

    def test_a_photo_is_saved_and_has_a_link(self):
        response = self.send_photo()

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["body"], "")
        self.assertEqual(
            response.data["image_url"], self.image_url(response.data["id"])
        )
        self.assertTrue(Message.objects.get().has_image)
        self.assertEqual(MessageImage.objects.count(), 1)

    def test_a_photo_can_carry_a_caption(self):
        response = self.send_photo(body="  Look at this  ")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["body"], "Look at this")
        self.assertIsNotNone(response.data["image_url"])

    def test_messages_without_a_photo_have_no_link(self):
        self.assertIsNone(self.send("Hi").data["image_url"])
        self.assertIsNone(self.send_sticker("heart").data["image_url"])
        self.assertFalse(MessageImage.objects.exists())

    def test_a_big_photo_is_shrunk_and_saved_as_a_jpeg(self):
        response = self.send_photo(picture(3200, 2400))

        stored = MessageImage.objects.get()
        shrunk = Image.open(io.BytesIO(bytes(stored.data)))
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(shrunk.format, "JPEG")
        self.assertEqual(max(shrunk.size), 1600)
        self.assertEqual((stored.width, stored.height), shrunk.size)

    def test_a_small_photo_is_not_enlarged(self):
        self.send_photo(picture(40, 30))

        stored = MessageImage.objects.get()
        self.assertEqual((stored.width, stored.height), (40, 30))

    def test_something_that_is_not_a_picture_is_refused(self):
        fake = SimpleUploadedFile("a.png", b"not a picture", content_type="image/png")

        response = self.send_photo(fake)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Message.objects.count(), 0)

    def test_a_photo_over_8_mb_is_refused(self):
        huge = SimpleUploadedFile(
            "big.jpg", b"0" * (8 * 1024 * 1024 + 1), content_type="image/jpeg"
        )

        response = self.send_photo(huge)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("8 MB", str(response.data))
        self.assertEqual(Message.objects.count(), 0)

    def test_a_sticker_cannot_travel_with_a_photo_or_text(self):
        with_photo = self.client.post(
            self.url("messages"),
            {"image": picture(), "sticker": "heart"},
            format="multipart",
        )
        with_text = self.client.post(
            self.url("messages"), {"body": "Hi", "sticker": "heart"}, format="json"
        )

        self.assertEqual(with_photo.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(with_text.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Message.objects.count(), 0)

    def test_both_people_can_open_the_photo(self):
        sent = self.send_photo(picture(3200, 2400)).data

        for user in (self.me, self.sarah):
            self.client.force_authenticate(user)
            response = self.client.get(self.image_url(sent["id"]))

            self.assertEqual(response.status_code, status.HTTP_200_OK)
            self.assertEqual(response["Content-Type"], "image/jpeg")
            self.assertIn("no-store", response["Cache-Control"])
            self.assertEqual(Image.open(io.BytesIO(response.content)).format, "JPEG")

    def test_the_other_person_sees_the_link_in_the_chat_and_the_list(self):
        sent = self.send_photo().data

        self.client.force_authenticate(self.sarah)
        shown = self.messages()["messages"][0]
        row = self.client.get(reverse("community:conversations")).data[0]

        self.assertEqual(shown["image_url"], self.image_url(sent["id"]))
        self.assertEqual(row["last_message"]["image_url"], self.image_url(sent["id"]))
        self.assertEqual(row["unread_count"], 1)

    def test_strangers_and_signed_out_people_cannot_open_it(self):
        sent = self.send_photo().data

        self.client.force_authenticate(self.daniel)
        self.assertEqual(
            self.client.get(self.image_url(sent["id"])).status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.client.force_authenticate(None)
        self.assertEqual(
            self.client.get(self.image_url(sent["id"])).status_code,
            status.HTTP_401_UNAUTHORIZED,
        )

    def test_a_photo_cannot_be_reached_through_another_conversation(self):
        self.pending.delete()
        other = Connection.objects.create(
            requester=self.me, addressee=self.daniel, status=Connection.Status.ACCEPTED
        )
        sent = self.send_photo().data

        response = self.client.get(self.image_url(sent["id"], conversation=other.id))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_a_message_without_a_photo_has_no_image_page(self):
        sent = self.send("Hi").data

        response = self.client.get(self.image_url(sent["id"]))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_the_other_person_is_notified_in_words(self):
        self.send_photo()
        self.send_photo(body="Sunset")

        bodies = set(Notification.objects.values_list("body", flat=True))
        self.assertEqual(bodies, {"Sent you a photo", "Sunset"})

    def test_removing_the_connection_removes_the_photos(self):
        self.send_photo()

        self.link.delete()

        self.assertFalse(MessageImage.objects.exists())


class NotificationTests(MessagingBase):
    def test_sending_a_message_notifies_the_other_person(self):
        self.send("Are you free tomorrow?")

        notification = Notification.objects.get()
        self.assertEqual(notification.user, self.sarah)
        self.assertEqual(notification.category, Notification.Category.CHAT_MESSAGE)
        self.assertEqual(notification.data["connection_id"], self.link.id)

    def test_the_sender_is_not_notified(self):
        self.send("Hello")

        self.assertFalse(Notification.objects.filter(user=self.me).exists())
