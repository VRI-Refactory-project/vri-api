"""V2-011: the session WebSocket lets in only the people on the session, and passes on its events."""

from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TransactionTestCase, override_settings

from apps.realtime.consumers import CLOSE_BAD_TICKET, CLOSE_NOT_ALLOWED
from apps.realtime.events import send_to_session
from apps.realtime.tickets import issue_ticket
from apps.service_requests.models import ServiceRequest
from apps.service_requests.services import create_request, respond_to_offer
from apps.service_requests.test_offers import make_interpreter, make_user
from config.asgi import application

User = get_user_model()
Status = ServiceRequest.Status


# The consumer reads the database from another thread, which only sees committed data, hence
# TransactionTestCase. In memory, so the tests need no Redis.
@override_settings(CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}})
class SessionSocketTests(TransactionTestCase):
    def setUp(self):
        self.interpreter = make_interpreter()
        self.deaf = make_user(User.Role.DEAF_HOH)
        self.session, _ = create_request(requester=self.deaf)

    def accept(self):
        respond_to_offer(self.session.offers.get(), "accept")
        self.session.refresh_from_db()

    async def open(self, user=None, ticket=None):
        if ticket is None:
            ticket = issue_ticket(user)
        socket = WebsocketCommunicator(application, f"/ws/sessions/{self.session.pk}/?ticket={ticket}")
        connected, _ = await socket.connect()
        self.assertTrue(connected)
        return socket

    async def assert_closed_with(self, socket, code):
        self.assertEqual(await socket.receive_output(), {"type": "websocket.close", "code": code})

    def snapshot(self, status):
        return {"type": "session.status", "session": self.session.pk, "status": status}

    # --- Who gets in ---

    async def test_the_requester_gets_the_status_while_waiting(self):
        socket = await self.open(self.deaf)

        self.assertEqual(await socket.receive_json_from(), self.snapshot(Status.OFFERED))
        await socket.disconnect()

    async def test_the_assigned_interpreter_gets_the_status(self):
        await sync_to_async(self.accept)()

        socket = await self.open(self.interpreter.user)

        self.assertEqual(await socket.receive_json_from(), self.snapshot(Status.ASSIGNED))
        await socket.disconnect()

    # --- Who stays out ---

    async def test_an_interpreter_who_was_only_offered_it_is_refused(self):
        socket = await self.open(self.interpreter.user)

        await self.assert_closed_with(socket, CLOSE_NOT_ALLOWED)

    async def test_another_user_is_refused(self):
        other = await sync_to_async(make_user)(User.Role.DEAF_HOH)

        socket = await self.open(other)

        await self.assert_closed_with(socket, CLOSE_NOT_ALLOWED)

    async def test_a_bad_ticket_is_refused(self):
        socket = await self.open(ticket="not-a-ticket")

        await self.assert_closed_with(socket, CLOSE_BAD_TICKET)

    async def test_no_ticket_is_refused(self):
        socket = await self.open(ticket="")

        await self.assert_closed_with(socket, CLOSE_BAD_TICKET)

    # --- send_to_session ---

    async def test_send_to_session_reaches_the_people_connected(self):
        socket = await self.open(self.deaf)
        await socket.receive_json_from()  # the snapshot

        await sync_to_async(send_to_session)(self.session.pk, "session.status", {"status": "ASSIGNED"})

        self.assertEqual(await socket.receive_json_from(), self.snapshot("ASSIGNED"))
        await socket.disconnect()

    async def test_send_to_session_skips_other_sessions(self):
        socket = await self.open(self.deaf)
        await socket.receive_json_from()

        await sync_to_async(send_to_session)(self.session.pk + 1, "session.status", {"status": "ASSIGNED"})

        self.assertTrue(await socket.receive_nothing())
        await socket.disconnect()

    async def test_nothing_is_sent_when_the_change_is_rolled_back(self):
        socket = await self.open(self.deaf)
        await socket.receive_json_from()

        def change_then_fail():
            with transaction.atomic():
                send_to_session(self.session.pk, "session.status", {"status": "ASSIGNED"})
                transaction.set_rollback(True)

        await sync_to_async(change_then_fail)()

        self.assertTrue(await socket.receive_nothing())
        await socket.disconnect()
