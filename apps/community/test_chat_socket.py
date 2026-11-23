"""The chat WebSocket: people hear about new, deleted and read messages as they happen."""

from unittest import mock

from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.community.models import Connection
from apps.community.test_connections import make_user
from apps.realtime.consumers import CLOSE_BAD_TICKET, CLOSE_NOT_ALLOWED
from apps.realtime.tickets import issue_ticket
from config.asgi import application


# The socket reads the database from another thread, which only sees committed data, hence
# TransactionTestCase. In memory, so the tests need no Redis.
@override_settings(
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
)
class ChatSocketTests(TransactionTestCase):
    def setUp(self):
        self.me = make_user("+256700000501", "Irene Test")
        self.sarah = make_user("+256700000502", "Sarah Nsubuga")
        self.daniel = make_user("+256700000503", "Daniel Ochieng")
        self.link = Connection.objects.create(
            requester=self.me, addressee=self.sarah, status=Connection.Status.ACCEPTED
        )

    async def open(self, user=None, ticket=None):
        socket = WebsocketCommunicator(
            application, f"/ws/community/?ticket={ticket or issue_ticket(user)}"
        )
        connected, _ = await socket.connect()
        self.assertTrue(connected)
        return socket

    async def ready(self, user):
        socket = await self.open(user)
        self.assertEqual(await socket.receive_json_from(), {"type": "community.ready"})
        return socket

    def api(self, user, method, name, *args, **data):
        client = APIClient()
        client.force_authenticate(user)
        return getattr(client, method)(
            reverse(f"community:{name}", args=args), data or None, format="json"
        )

    # --- Who gets in ---

    async def test_a_bad_ticket_is_closed_so_the_app_gets_a_new_one(self):
        socket = await self.open(ticket="nonsense")

        self.assertEqual(
            await socket.receive_output(),
            {"type": "websocket.close", "code": CLOSE_BAD_TICKET},
        )

    async def test_only_deaf_users_may_listen(self):
        interpreter = await sync_to_async(make_user)(
            "+256700000510", "An Interpreter", role="interpreter"
        )

        socket = await self.open(interpreter)

        self.assertEqual(
            await socket.receive_output(),
            {"type": "websocket.close", "code": CLOSE_NOT_ALLOWED},
        )

    # --- What they hear ---

    async def test_both_people_hear_a_new_message_from_their_own_side(self):
        mine, hers = await self.ready(self.me), await self.ready(self.sarah)

        sent = await sync_to_async(self.api)(
            self.me, "post", "messages", self.link.id, body="Hi Sarah"
        )

        for socket, by_me in ((mine, True), (hers, False)):
            event = await socket.receive_json_from()
            self.assertEqual(event["type"], "community.message")
            self.assertEqual(event["connection"], self.link.id)
            self.assertEqual(event["message"]["id"], sent.data["id"])
            self.assertEqual(event["message"]["body"], "Hi Sarah")
            self.assertEqual(event["message"]["sent_by_me"], by_me)
            await socket.disconnect()

    async def test_a_stranger_hears_nothing(self):
        outsider = await self.ready(self.daniel)

        await sync_to_async(self.api)(
            self.me, "post", "messages", self.link.id, body="private"
        )

        self.assertTrue(await outsider.receive_nothing())
        await outsider.disconnect()

    async def test_both_people_hear_a_deletion(self):
        sent = await sync_to_async(self.api)(
            self.me, "post", "messages", self.link.id, body="oops"
        )
        mine, hers = await self.ready(self.me), await self.ready(self.sarah)

        await sync_to_async(self.api)(
            self.me, "delete", "message-detail", self.link.id, sent.data["id"]
        )

        for socket in (mine, hers):
            self.assertEqual(
                await socket.receive_json_from(),
                {
                    "type": "community.message_deleted",
                    "connection": self.link.id,
                    "message_id": sent.data["id"],
                },
            )
            await socket.disconnect()

    async def test_the_sender_hears_when_the_message_is_read(self):
        sent = await sync_to_async(self.api)(
            self.me, "post", "messages", self.link.id, body="read me"
        )
        mine, hers = await self.ready(self.me), await self.ready(self.sarah)

        await sync_to_async(self.api)(
            self.sarah, "post", "conversation-read", self.link.id
        )

        self.assertEqual(
            await mine.receive_json_from(),
            {
                "type": "community.read",
                "connection": self.link.id,
                "message_ids": [sent.data["id"]],
            },
        )
        # The reader already knows, and a second "read" with nothing new says nothing.
        self.assertTrue(await hers.receive_nothing())
        await sync_to_async(self.api)(
            self.sarah, "post", "conversation-read", self.link.id
        )
        self.assertTrue(await mine.receive_nothing())
        await mine.disconnect()
        await hers.disconnect()

    async def test_every_device_of_a_person_hears_it(self):
        phone, laptop = await self.ready(self.sarah), await self.ready(self.sarah)

        await sync_to_async(self.api)(
            self.me, "post", "messages", self.link.id, body="both devices"
        )

        for socket in (phone, laptop):
            self.assertEqual(
                (await socket.receive_json_from())["message"]["body"], "both devices"
            )
            await socket.disconnect()

    # --- A broken push never breaks the chat ---

    async def test_a_message_still_sends_when_the_push_fails(self):
        with mock.patch(
            "apps.community.events.get_channel_layer", side_effect=RuntimeError("down")
        ):
            sent = await sync_to_async(self.api)(
                self.me, "post", "messages", self.link.id, body="still sent"
            )

        self.assertEqual(sent.status_code, 201)
