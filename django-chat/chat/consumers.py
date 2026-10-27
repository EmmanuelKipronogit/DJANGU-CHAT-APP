"""
WebSocket protocol (JSON), one socket per signed-in, verified user.

Client -> server
  {"type": "message", "conversation": "<uuid>", "body": "...", "client_id": "..."}
  {"type": "typing",  "conversation": "<uuid>"}
  {"type": "read",    "conversation": "<uuid>"}

Server -> client
  {"type": "message", "message": {...}}              new message (also echoed to the sender)
  {"type": "typing", "conversation": "<uuid>", "user": {"id": 1, "name": "..."}}
  {"type": "conversation.added", "conversation": "<uuid>"}
  {"type": "error", "message": "...", "client_id": "..."}

Every conversation is a Channels group, so a message is published once and the
channel layer (Redis in production) fans it out to every member's socket,
across any number of server processes.
"""
import time
import uuid
from collections import deque

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings

from accounts import two_factor

from . import services
from .services import ChatError

CLOSE_UNAUTHORIZED = 4401


def conv_group(conversation_id):
    return f"conv_{uuid.UUID(str(conversation_id)).hex}"


class ChatConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        session = self.scope.get("session")
        verified = False
        if session is not None:
            verified = await database_sync_to_async(two_factor.is_verified)(session)
        if user is None or not user.is_authenticated or not verified:
            await self.close(code=CLOSE_UNAUTHORIZED)
            return

        self.user = user
        self.conversation_ids = set()
        self.sent_at = deque()

        await self.channel_layer.group_add(f"user_{user.pk}", self.channel_name)
        for conversation_id in await database_sync_to_async(services.conversation_ids_for)(user):
            await self._subscribe(conversation_id)
        await self.accept()

    async def disconnect(self, code):
        if not hasattr(self, "user"):
            return
        await self.channel_layer.group_discard(f"user_{self.user.pk}", self.channel_name)
        for conversation_id in list(self.conversation_ids):
            await self.channel_layer.group_discard(conv_group(conversation_id), self.channel_name)

    # ------------------------------------------------------------ subscriptions
    async def _subscribe(self, conversation_id):
        conversation_id = str(conversation_id)
        if conversation_id not in self.conversation_ids:
            self.conversation_ids.add(conversation_id)
            await self.channel_layer.group_add(conv_group(conversation_id), self.channel_name)

    # ----------------------------------------------------------- client -> server
    async def receive_json(self, content, **kwargs):
        if not isinstance(content, dict):
            return
        kind = content.get("type")
        conversation_id = str(content.get("conversation") or "")
        try:
            uuid.UUID(conversation_id)
        except ValueError:
            await self.send_json({"type": "error", "message": "Bad conversation id."})
            return

        if kind == "message":
            await self._handle_message(conversation_id, content)
        elif kind == "typing" and conversation_id in self.conversation_ids:
            await self.channel_layer.group_send(
                conv_group(conversation_id),
                {
                    "type": "chat.typing",
                    "conversation": conversation_id,
                    "user": {"id": self.user.pk, "name": self.user.name},
                },
            )
        elif kind == "read":
            await database_sync_to_async(services.mark_read)(self.user, conversation_id)

    def _rate_limited(self):
        now = time.monotonic()
        window = settings.CHAT_RATE_LIMIT_WINDOW_SECONDS
        while self.sent_at and now - self.sent_at[0] > window:
            self.sent_at.popleft()
        if len(self.sent_at) >= settings.CHAT_RATE_LIMIT_MESSAGES:
            return True
        self.sent_at.append(now)
        return False

    async def _handle_message(self, conversation_id, content):
        client_id = str(content.get("client_id") or "")[:64]
        if self._rate_limited():
            await self.send_json(
                {"type": "error", "message": "You're sending messages too fast.", "client_id": client_id}
            )
            return
        try:
            message = await database_sync_to_async(self._save)(conversation_id, content.get("body"), client_id)
        except ChatError as exc:
            await self.send_json({"type": "error", "message": str(exc), "client_id": client_id})
            return
        await self.channel_layer.group_send(
            conv_group(conversation_id), {"type": "chat.message", "message": message}
        )

    def _save(self, conversation_id, body, client_id):
        msg = services.post_message(self.user, conversation_id, body)
        return services.serialize_message(msg, client_id=client_id)

    # ----------------------------------------------------------- server -> client
    async def chat_message(self, event):
        message = dict(event["message"])
        # client_id only matters to the sender (to match its optimistic bubble).
        if message["sender"]["id"] != self.user.pk:
            message.pop("client_id", None)
        await self.send_json({"type": "message", "message": message})

    async def chat_typing(self, event):
        if event["user"]["id"] != self.user.pk:
            await self.send_json(
                {"type": "typing", "conversation": event["conversation"], "user": event["user"]}
            )

    async def user_conversation_added(self, event):
        await self._subscribe(event["conversation_id"])
        await self.send_json({"type": "conversation.added", "conversation": event["conversation_id"]})
