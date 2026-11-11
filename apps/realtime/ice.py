"""STUN and TURN servers the call apps use to connect (V2-011, INFRA-03).

STUN finds the public address of a device. TURN relays the call when a direct connection is blocked.
Both come from settings, so nothing is hard-coded here.
"""

from django.conf import settings


def ice_servers():
    """The list of ICE servers for a call. Empty when none are configured."""
    servers = []
    if settings.LIVEKIT_STUN_URLS:
        servers.append({"urls": list(settings.LIVEKIT_STUN_URLS)})
    if settings.LIVEKIT_TURN_URLS:
        servers.append(
            {
                "urls": list(settings.LIVEKIT_TURN_URLS),
                "username": settings.LIVEKIT_TURN_USERNAME,
                "credential": settings.LIVEKIT_TURN_CREDENTIAL,
            }
        )
    return servers
