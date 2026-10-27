import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class Conversation(models.Model):
    """A direct (1:1) conversation or a group conversation."""

    class Kind(models.TextChoices):
        DIRECT = "direct", "Direct"
        GROUP = "group", "Group"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    name = models.CharField(max_length=80, blank=True)  # groups only
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(default=timezone.now)
    # Bumped on every message so the sidebar can sort by recent activity.
    updated_at = models.DateTimeField(default=timezone.now, db_index=True)
    # "<low_user_id>:<high_user_id>" - guarantees one DM per pair of users.
    direct_key = models.CharField(max_length=64, unique=True, null=True, blank=True)

    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL, through="Membership", related_name="conversations"
    )

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.name or f"{self.get_kind_display()} {self.pk}"

    @property
    def group_name(self):
        """Channels group every member's socket joins for this conversation."""
        return f"conv_{self.pk.hex}"


class Membership(models.Model):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        MEMBER = "member", "Member"

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.MEMBER)
    joined_at = models.DateTimeField(default=timezone.now)
    # Everything at or before this time counts as read (drives unread badges).
    last_read_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["conversation", "user"], name="unique_membership"),
        ]
        indexes = [models.Index(fields=["user", "conversation"])]

    def __str__(self):
        return f"{self.user_id} in {self.conversation_id}"


class Message(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    body = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["conversation", "-id"])]

    def __str__(self):
        return f"{self.sender_id}: {self.body[:30]}"
