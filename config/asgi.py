"""ASGI entry point. HTTP goes to Django; WebSockets go to Channels (V2-011)."""

import os

from channels.routing import ProtocolTypeRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

# Set Django up before importing anything that uses models (the WebSocket routes, added in step 4).
django_asgi_app = get_asgi_application()

application = ProtocolTypeRouter({"http": django_asgi_app})
