"""ASGI entry point. HTTP goes to Django; WebSockets go to Channels (V2-011)."""

import os

from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

# Set Django up before importing anything that uses models (the WebSocket routes).
django_asgi_app = get_asgi_application()

from apps.realtime.routing import websocket_urlpatterns  # noqa: E402

# No Origin check: the mobile app sends no Origin header, and a page on another site cannot get a
# ticket, since that needs the user's login token.
application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": URLRouter(websocket_urlpatterns),
    }
)
