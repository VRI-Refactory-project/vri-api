from django.urls import path

from apps.community.consumers import CommunityConsumer
from apps.realtime.consumers import SessionConsumer

websocket_urlpatterns = [
    path("ws/sessions/<int:session_id>/", SessionConsumer.as_asgi()),
    path("ws/community/", CommunityConsumer.as_asgi()),
]
