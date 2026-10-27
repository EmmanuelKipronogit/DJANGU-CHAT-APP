from django.urls import path

from . import consumers

websocket_urlpatterns = [
    # One socket per user carries every conversation they're in.
    path("ws/chat/", consumers.ChatConsumer.as_asgi()),
]
