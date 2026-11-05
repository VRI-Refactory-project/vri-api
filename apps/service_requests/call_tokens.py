import asyncio
from datetime import timedelta

from django.conf import settings
from livekit import api


def room_name(service_request):
    return f"session-{service_request.pk}"


def create_call_token(service_request, user):
    """A LiveKit token that lets this user join this session's room and nothing else."""
    grants = api.VideoGrants(
        room_join=True,
        room=room_name(service_request),
        can_publish=True,
        can_subscribe=True,
        can_publish_data=True,
    )
    return (
        api.AccessToken(settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
        .with_identity(f"user-{user.pk}")
        .with_name(user.full_name)
        .with_ttl(timedelta(minutes=10))
        .with_grants(grants)
        .to_jwt()
    )


def create_guest_token(service_request, invitation):
    """Same room as the interpreter and Deaf user, but the identity is a guest, not a user."""
    grants = api.VideoGrants(
        room_join=True,
        room=room_name(service_request),  # only this session's room
        can_publish=True,  # the guest app decides: video, or audio only
        can_subscribe=True,
        can_publish_data=True,
    )
    return (
        api.AccessToken(settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET)
        .with_identity(f"guest-{invitation.pk}")
        .with_name("Hearing guest")
        .with_ttl(timedelta(minutes=10))
        .with_grants(grants)
        .to_jwt()
    )


async def _identities_in_room(room):
    # LiveKit's server API is async, so we wrap it below to call it from normal Django views.
    async with api.LiveKitAPI(
        settings.LIVEKIT_URL, settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET
    ) as lkapi:
        result = await lkapi.room.list_participants(
            api.ListParticipantsRequest(room=room)
        )
        return [p.identity for p in result.participants]


def interpreter_is_in_room(service_request):
    """True when the assigned interpreter is connected to the call right now."""
    if service_request.interpreter is None:
        return False
    wanted = f"user-{service_request.interpreter.user_id}"  # same identity create_call_token uses
    try:
        return wanted in asyncio.run(_identities_in_room(room_name(service_request)))
    except Exception:
        return False  # if LiveKit cannot be reached, the guest simply keeps waiting


async def _remove(room, identity):
    async with api.LiveKitAPI(
        settings.LIVEKIT_URL, settings.LIVEKIT_API_KEY, settings.LIVEKIT_API_SECRET
    ) as lkapi:
        await lkapi.room.remove_participant(
            api.RoomParticipantIdentity(room=room, identity=identity)
        )


def remove_guest_from_room(service_request, invitation):
    """Kick the guest out of the live call. If they are not in it, nothing happens."""
    try:
        asyncio.run(_remove(room_name(service_request), f"guest-{invitation.pk}"))
    except Exception:
        pass
