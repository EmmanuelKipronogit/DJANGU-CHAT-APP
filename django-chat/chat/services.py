"""
Chat business logic.

Views and the WebSocket consumer stay thin and call into here, so adding
another front end (mobile API, bots, management commands) later reuses the
same rules.
"""
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, F, OuterRef, Q, Subquery
from django.utils import timezone

from accounts.models import User

from .models import Conversation, Membership, Message


class ChatError(Exception):
    """A rule was broken; the message is safe to show to the user."""


# ---------------------------------------------------------------- conversations
def _direct_key(a_id, b_id):
    low, high = sorted((a_id, b_id))
    return f"{low}:{high}"


@transaction.atomic
def get_or_create_direct(user, other):
    if user.pk == other.pk:
        raise ChatError("You can't start a conversation with yourself.")
    if not other.is_active:
        raise ChatError("That user isn't available.")

    key = _direct_key(user.pk, other.pk)
    existing = Conversation.objects.filter(direct_key=key).first()
    if existing:
        return existing, False
    try:
        with transaction.atomic():
            convo = Conversation.objects.create(
                kind=Conversation.Kind.DIRECT, direct_key=key, created_by=user
            )
            Membership.objects.bulk_create(
                [Membership(conversation=convo, user=user), Membership(conversation=convo, user=other)]
            )
    except IntegrityError:  # lost a race with the other participant
        return Conversation.objects.get(direct_key=key), False
    return convo, True


@transaction.atomic
def create_group(creator, name, member_ids):
    name = (name or "").strip()
    if not name:
        raise ChatError("Give the group a name.")
    if len(name) > 80:
        raise ChatError("Group names can be at most 80 characters.")

    others = list(User.objects.filter(pk__in=set(member_ids), is_active=True).exclude(pk=creator.pk))
    if not others:
        raise ChatError("Add at least one other person.")
    if len(others) + 1 > settings.CHAT_GROUP_MAX_MEMBERS:
        raise ChatError(f"Groups can have at most {settings.CHAT_GROUP_MAX_MEMBERS} members.")

    convo = Conversation.objects.create(kind=Conversation.Kind.GROUP, name=name, created_by=creator)
    Membership.objects.create(conversation=convo, user=creator, role=Membership.Role.ADMIN)
    Membership.objects.bulk_create([Membership(conversation=convo, user=u) for u in others])
    return convo, [creator, *others]


@transaction.atomic
def add_members(actor, convo, user_ids):
    if convo.kind != Conversation.Kind.GROUP:
        raise ChatError("You can only add people to groups.")
    if not Membership.objects.filter(
        conversation=convo, user=actor, role=Membership.Role.ADMIN
    ).exists():
        raise ChatError("Only group admins can add people.")

    current = set(convo.memberships.values_list("user_id", flat=True))
    new_users = list(User.objects.filter(pk__in=set(user_ids), is_active=True).exclude(pk__in=current))
    if not new_users:
        raise ChatError("Nobody new to add.")
    if len(current) + len(new_users) > settings.CHAT_GROUP_MAX_MEMBERS:
        raise ChatError(f"Groups can have at most {settings.CHAT_GROUP_MAX_MEMBERS} members.")

    Membership.objects.bulk_create([Membership(conversation=convo, user=u) for u in new_users])
    return new_users


def get_membership(user, conversation_id):
    return (
        Membership.objects.select_related("conversation")
        .filter(user=user, conversation_id=conversation_id)
        .first()
    )


def conversation_ids_for(user):
    return [str(pk) for pk in Membership.objects.filter(user=user).values_list("conversation_id", flat=True)]


def conversations_for(user):
    """Sidebar data: the user's conversations with last message + unread count."""
    last = Message.objects.filter(conversation=OuterRef("conversation_id")).order_by("-id")
    memberships = (
        Membership.objects.filter(user=user)
        .select_related("conversation")
        .annotate(
            last_body=Subquery(last.values("body")[:1]),
            last_at=Subquery(last.values("created_at")[:1]),
            unread=Count(
                "conversation__messages",
                filter=Q(conversation__messages__created_at__gt=F("last_read_at"))
                & ~Q(conversation__messages__sender=user),
            ),
        )
        .order_by("-conversation__updated_at")
    )
    convos = []
    for m in memberships:
        c = m.conversation
        c.last_body, c.last_at, c.unread = m.last_body, m.last_at, m.unread
        convos.append(c)
    return convos


def display_name_for(convo, viewer, members=None):
    """Group name, or the other person's name for a DM."""
    if convo.kind == Conversation.Kind.GROUP:
        return convo.name or "Group"
    members = members if members is not None else convo.members.all()
    for member in members:
        if member.pk != viewer.pk:
            return member.name
    return "Direct message"


def sidebar_items(user):
    """JSON-friendly sidebar rows."""
    convos = conversations_for(user)
    # One query for all DM counterparts instead of one per conversation.
    direct_ids = [c.pk for c in convos if c.kind == Conversation.Kind.DIRECT]
    counterpart = {}
    if direct_ids:
        for m in (
            Membership.objects.filter(conversation_id__in=direct_ids)
            .exclude(user=user)
            .select_related("user")
        ):
            counterpart[m.conversation_id] = m.user
    items = []
    for c in convos:
        if c.kind == Conversation.Kind.DIRECT:
            other = counterpart.get(c.pk)
            title = other.name if other else "Direct message"
        else:
            title = c.name or "Group"
        items.append(
            {
                "id": str(c.pk),
                "kind": c.kind,
                "title": title,
                "last_body": (c.last_body or "")[:120],
                "last_at": c.last_at.isoformat() if c.last_at else None,
                "unread": c.unread,
            }
        )
    return items


# --------------------------------------------------------------------- messages
def serialize_message(msg, client_id=None):
    data = {
        "id": msg.pk,
        "conversation": str(msg.conversation_id),
        "sender": {"id": msg.sender_id, "name": msg.sender.name if msg.sender else "Deleted user"},
        "body": msg.body,
        "created_at": msg.created_at.isoformat(),
    }
    if client_id:
        data["client_id"] = client_id
    return data


def clean_body(body):
    body = (body or "").strip()
    if not body:
        raise ChatError("Message is empty.")
    if len(body) > settings.CHAT_MESSAGE_MAX_LENGTH:
        raise ChatError(f"Messages can be at most {settings.CHAT_MESSAGE_MAX_LENGTH} characters.")
    return body


@transaction.atomic
def post_message(user, conversation_id, body):
    """Persist a message from a member. Raises ChatError if not allowed."""
    body = clean_body(body)
    membership = get_membership(user, conversation_id)
    if membership is None:
        raise ChatError("You're not in that conversation.")

    now = timezone.now()
    msg = Message.objects.create(
        conversation_id=conversation_id, sender=user, body=body, created_at=now
    )
    Conversation.objects.filter(pk=conversation_id).update(updated_at=now)
    # Sending implies you've read everything up to now.
    Membership.objects.filter(pk=membership.pk).update(last_read_at=now)
    msg.sender = user
    return msg


def history(user, conversation_id, before_id=None, limit=None):
    """Messages oldest -> newest, paginated backwards from `before_id`."""
    if get_membership(user, conversation_id) is None:
        raise ChatError("You're not in that conversation.")
    limit = limit or settings.CHAT_HISTORY_PAGE_SIZE
    qs = Message.objects.filter(conversation_id=conversation_id).select_related("sender")
    if before_id:
        qs = qs.filter(pk__lt=before_id)
    rows = list(qs.order_by("-id")[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    rows.reverse()
    return [serialize_message(m) for m in rows], has_more


def mark_read(user, conversation_id):
    return Membership.objects.filter(user=user, conversation_id=conversation_id).update(
        last_read_at=timezone.now()
    )


# ------------------------------------------------------------------------ users
def search_users(requester, query, limit=8):
    query = (query or "").strip()
    if len(query) < 3:
        return []
    return list(
        User.objects.filter(is_active=True)
        .exclude(pk=requester.pk)
        .filter(Q(email__icontains=query) | Q(display_name__icontains=query))
        .order_by("display_name", "email")[:limit]
    )
