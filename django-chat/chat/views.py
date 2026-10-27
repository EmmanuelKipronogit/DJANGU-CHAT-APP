import json
from functools import wraps

from django.conf import settings
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from accounts.models import User

from . import realtime, services
from .models import Conversation, Membership
from .services import ChatError


# ------------------------------------------------------------------ helpers
def api(view):
    """JSON endpoint: 401 for anonymous users, ChatError -> 400, parsed body in request.json."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({"error": "unauthenticated"}, status=401)
        request.json = {}
        if request.method == "POST" and request.body:
            try:
                request.json = json.loads(request.body)
                if not isinstance(request.json, dict):
                    raise ValueError
            except ValueError:
                return JsonResponse({"error": "Invalid JSON."}, status=400)
        try:
            return view(request, *args, **kwargs)
        except ChatError as exc:
            return JsonResponse({"error": str(exc)}, status=400)

    return wrapper


def _int_list(values):
    try:
        return [int(v) for v in (values or [])]
    except (TypeError, ValueError):
        raise ChatError("Invalid user list.")


def _conversation_payload(convo, viewer):
    members = list(convo.members.all())
    is_admin = convo.memberships.filter(user=viewer, role=Membership.Role.ADMIN).exists()
    return {
        "id": str(convo.pk),
        "kind": convo.kind,
        "title": services.display_name_for(convo, viewer, members),
        "is_admin": is_admin,
        "members": [{"id": m.pk, "name": m.name, "email": m.email} for m in members],
    }


# -------------------------------------------------------------------- pages
def _render_app(request, active_id=None):
    me = request.user
    active = None
    messages_payload, has_more = [], False

    if active_id:
        membership = services.get_membership(me, active_id)
        if membership is None:
            raise Http404("No such conversation")
        active = _conversation_payload(membership.conversation, me)
        messages_payload, has_more = services.history(me, active_id)
        services.mark_read(me, active_id)

    bootstrap = {
        "me": {"id": me.pk, "name": me.name},
        "conversations": services.sidebar_items(me),
        "active": active,
        "messages": messages_payload,
        "has_more": has_more,
        "limits": {"message_max_length": settings.CHAT_MESSAGE_MAX_LENGTH},
    }
    return render(request, "chat/app.html", {"bootstrap": bootstrap})


@ensure_csrf_cookie
def home(request):
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login

        return redirect_to_login(request.get_full_path())
    return _render_app(request)


@ensure_csrf_cookie
def room(request, conversation_id):
    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login

        return redirect_to_login(request.get_full_path())
    return _render_app(request, conversation_id)


# ---------------------------------------------------------------------- API
@api
@require_GET
def conversation_list(request):
    return JsonResponse({"conversations": services.sidebar_items(request.user)})


@api
@require_GET
def conversation_detail(request, conversation_id):
    membership = services.get_membership(request.user, conversation_id)
    if membership is None:
        raise ChatError("You're not in that conversation.")
    return JsonResponse({"conversation": _conversation_payload(membership.conversation, request.user)})


@api
@require_GET
def message_history(request, conversation_id):
    before = request.GET.get("before")
    try:
        before_id = int(before) if before else None
    except ValueError:
        raise ChatError("Invalid cursor.")
    messages, has_more = services.history(request.user, conversation_id, before_id=before_id)
    return JsonResponse({"messages": messages, "has_more": has_more})


@api
@require_GET
def user_search(request):
    users = services.search_users(request.user, request.GET.get("q", ""))
    return JsonResponse(
        {"users": [{"id": u.pk, "name": u.name, "email": u.email} for u in users]}
    )


@api
@require_POST
def start_direct(request):
    try:
        other = User.objects.get(pk=int(request.json.get("user_id")), is_active=True)
    except (TypeError, ValueError, User.DoesNotExist):
        raise ChatError("That user doesn't exist.")
    convo, created = services.get_or_create_direct(request.user, other)
    if created:
        realtime.notify_conversation_added(convo.pk, [request.user.pk, other.pk])
    return JsonResponse({"id": str(convo.pk), "created": created})


@api
@require_POST
def create_group(request):
    convo, members = services.create_group(
        request.user, request.json.get("name"), _int_list(request.json.get("member_ids"))
    )
    realtime.notify_conversation_added(convo.pk, [m.pk for m in members])
    return JsonResponse({"id": str(convo.pk)}, status=201)


@api
@require_POST
def add_members(request, conversation_id):
    membership = services.get_membership(request.user, conversation_id)
    if membership is None:
        raise ChatError("You're not in that conversation.")
    new_users = services.add_members(
        request.user, membership.conversation, _int_list(request.json.get("user_ids"))
    )
    realtime.notify_conversation_added(conversation_id, [u.pk for u in new_users])
    return JsonResponse({"added": [u.pk for u in new_users]})


@api
@require_POST
def mark_read(request, conversation_id):
    services.mark_read(request.user, conversation_id)
    return JsonResponse({"ok": True})
