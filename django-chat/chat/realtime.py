"""Helpers for pushing events to connected sockets from ordinary (sync) code."""
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def user_group(user_id):
    return f"user_{user_id}"


def notify_conversation_added(conversation_id, user_ids):
    """Tell these users' open sockets to subscribe to a new conversation."""
    layer = get_channel_layer()
    for user_id in user_ids:
        async_to_sync(layer.group_send)(
            user_group(user_id),
            {"type": "user.conversation_added", "conversation_id": str(conversation_id)},
        )
