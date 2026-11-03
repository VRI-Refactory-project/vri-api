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
