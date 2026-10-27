import json
import uuid

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator

from accounts import two_factor
from chat import services
from chat.consumers import ChatConsumer
from chat.models import Conversation, Membership, Message
from chat.services import ChatError

pytestmark = pytest.mark.django_db


# ------------------------------------------------------------------ services
def test_direct_conversation_is_deduplicated_in_both_directions(alice, bob):
    first, created = services.get_or_create_direct(alice, bob)
    again, created_again = services.get_or_create_direct(bob, alice)
    assert created and not created_again
    assert first.pk == again.pk
    assert first.memberships.count() == 2


def test_cannot_dm_yourself(alice):
    with pytest.raises(ChatError):
        services.get_or_create_direct(alice, alice)


def test_create_group_makes_creator_admin(alice, bob, carol):
    convo, members = services.create_group(alice, "  Trip  ", [bob.pk, carol.pk, alice.pk])
    assert convo.name == "Trip"
    assert {m.pk for m in members} == {alice.pk, bob.pk, carol.pk}
    assert Membership.objects.get(conversation=convo, user=alice).role == Membership.Role.ADMIN
    assert Membership.objects.get(conversation=convo, user=bob).role == Membership.Role.MEMBER


def test_group_needs_name_and_other_members(alice, bob):
    with pytest.raises(ChatError):
        services.create_group(alice, "", [bob.pk])
    with pytest.raises(ChatError):
        services.create_group(alice, "Solo", [])


def test_only_admins_can_add_members(alice, bob, carol):
    convo, _ = services.create_group(alice, "Team", [bob.pk])
    with pytest.raises(ChatError):
        services.add_members(bob, convo, [carol.pk])
    added = services.add_members(alice, convo, [carol.pk])
    assert [u.pk for u in added] == [carol.pk]


def test_non_members_cannot_post_or_read(alice, bob, carol):
    convo, _ = services.get_or_create_direct(alice, bob)
    with pytest.raises(ChatError):
        services.post_message(carol, convo.pk, "hi")
    with pytest.raises(ChatError):
        services.history(carol, convo.pk)


def test_message_validation(alice, bob, settings):
    convo, _ = services.get_or_create_direct(alice, bob)
    with pytest.raises(ChatError):
        services.post_message(alice, convo.pk, "   ")
    settings.CHAT_MESSAGE_MAX_LENGTH = 10
    with pytest.raises(ChatError):
        services.post_message(alice, convo.pk, "x" * 11)


def test_unread_counts_and_mark_read(alice, bob):
    convo, _ = services.get_or_create_direct(alice, bob)
    services.post_message(alice, convo.pk, "one")
    services.post_message(alice, convo.pk, "two")

    bob_items = services.sidebar_items(bob)
    assert bob_items[0]["unread"] == 2
    assert bob_items[0]["title"] == "Alice"
    assert bob_items[0]["last_body"] == "two"
    # your own messages never count as unread
    assert services.sidebar_items(alice)[0]["unread"] == 0

    services.mark_read(bob, convo.pk)
    assert services.sidebar_items(bob)[0]["unread"] == 0


def test_history_paginates_backwards(alice, bob):
    convo, _ = services.get_or_create_direct(alice, bob)
    for i in range(7):
        services.post_message(alice, convo.pk, f"m{i}")

    page, has_more = services.history(alice, convo.pk, limit=3)
    assert [m["body"] for m in page] == ["m4", "m5", "m6"] and has_more
    older, has_more = services.history(alice, convo.pk, before_id=page[0]["id"], limit=3)
    assert [m["body"] for m in older] == ["m1", "m2", "m3"] and has_more
    oldest, has_more = services.history(alice, convo.pk, before_id=older[0]["id"], limit=3)
    assert [m["body"] for m in oldest] == ["m0"] and not has_more


def test_user_search_needs_three_chars_and_excludes_self(alice, bob):
    assert services.search_users(alice, "bo") == []
    assert services.search_users(alice, "bob") == [bob]
    assert services.search_users(alice, "alice") == []


# ----------------------------------------------------------------------- API
def post_json(client, url, payload):
    return client.post(url, data=json.dumps(payload), content_type="application/json")


def test_api_requires_login(client):
    assert client.get("/api/conversations/").status_code == 401


def test_start_direct_and_group_via_api(verified_client, alice, bob, carol):
    res = post_json(verified_client, "/api/direct/", {"user_id": bob.pk})
    assert res.status_code == 200 and res.json()["created"] is True
    again = post_json(verified_client, "/api/direct/", {"user_id": bob.pk})
    assert again.json()["created"] is False and again.json()["id"] == res.json()["id"]

    group = post_json(verified_client, "/api/groups/", {"name": "Crew", "member_ids": [bob.pk, carol.pk]})
    assert group.status_code == 201

    listing = verified_client.get("/api/conversations/").json()["conversations"]
    assert {c["title"] for c in listing} == {"Bob", "Crew"}


def test_api_errors_are_400_with_message(verified_client):
    res = post_json(verified_client, "/api/groups/", {"name": "", "member_ids": []})
    assert res.status_code == 400
    assert "name" in res.json()["error"].lower()


def test_room_page_is_members_only(verified_client, bob, carol):
    convo, _ = services.get_or_create_direct(bob, carol)
    assert verified_client.get(f"/c/{convo.pk}/").status_code == 404
    assert verified_client.get(f"/api/conversations/{convo.pk}/messages/").status_code == 400


def test_room_page_renders_for_members(verified_client, alice, bob):
    convo, _ = services.get_or_create_direct(alice, bob)
    services.post_message(bob, convo.pk, "hello alice")
    res = verified_client.get(f"/c/{convo.pk}/")
    assert res.status_code == 200
    assert b"hello alice" in res.content


# ------------------------------------------------------------------- sockets
def make_socket(user, verified=True):
    communicator = WebsocketCommunicator(ChatConsumer.as_asgi(), "/ws/chat/")
    communicator.scope["user"] = user
    communicator.scope["session"] = {two_factor.SESSION_KEY: verified}
    return communicator


@pytest.mark.django_db(transaction=True)
async def test_socket_rejects_unverified_sessions(alice):
    communicator = make_socket(alice, verified=False)
    connected, code = await communicator.connect()
    assert not connected


@pytest.mark.django_db(transaction=True)
async def test_two_users_chat_in_real_time(alice, bob):
    convo, _ = await sync_to_async(services.get_or_create_direct)(alice, bob)

    a = make_socket(alice)
    b = make_socket(bob)
    assert (await a.connect())[0]
    assert (await b.connect())[0]

    await a.send_json_to({"type": "message", "conversation": str(convo.pk), "body": "hi bob", "client_id": "c1"})

    got_a = await a.receive_json_from()
    got_b = await b.receive_json_from()
    assert got_a["message"]["body"] == "hi bob"
    assert got_a["message"]["client_id"] == "c1"  # sender can match its optimistic bubble
    assert got_b["message"]["body"] == "hi bob"
    assert "client_id" not in got_b["message"]
    assert got_b["message"]["sender"]["name"] == "Alice"
    assert await sync_to_async(Message.objects.filter(conversation=convo).count)() == 1

    await a.disconnect()
    await b.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_group_messages_reach_every_member_but_not_outsiders(alice, bob, carol):
    convo, _ = await sync_to_async(services.create_group)(alice, "Team", [bob.pk])

    a, b, c = make_socket(alice), make_socket(bob), make_socket(carol)
    for s in (a, b, c):
        assert (await s.connect())[0]

    await b.send_json_to({"type": "message", "conversation": str(convo.pk), "body": "team update"})
    assert (await a.receive_json_from())["message"]["body"] == "team update"
    assert (await b.receive_json_from())["message"]["body"] == "team update"
    assert await c.receive_nothing()

    for s in (a, b, c):
        await s.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_outsider_cannot_post_into_a_conversation(alice, bob, carol):
    convo, _ = await sync_to_async(services.get_or_create_direct)(alice, bob)
    c = make_socket(carol)
    assert (await c.connect())[0]

    await c.send_json_to({"type": "message", "conversation": str(convo.pk), "body": "let me in"})
    reply = await c.receive_json_from()
    assert reply["type"] == "error"
    assert await sync_to_async(Message.objects.count)() == 0
    await c.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_typing_goes_to_others_only(alice, bob):
    convo, _ = await sync_to_async(services.get_or_create_direct)(alice, bob)
    a, b = make_socket(alice), make_socket(bob)
    await a.connect()
    await b.connect()

    await a.send_json_to({"type": "typing", "conversation": str(convo.pk)})
    event = await b.receive_json_from()
    assert event["type"] == "typing" and event["user"]["name"] == "Alice"
    assert await a.receive_nothing()

    await a.disconnect()
    await b.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_rate_limit(alice, bob, settings):
    settings.CHAT_RATE_LIMIT_MESSAGES = 2
    convo, _ = await sync_to_async(services.get_or_create_direct)(alice, bob)
    a = make_socket(alice)
    await a.connect()

    for i in range(3):
        await a.send_json_to({"type": "message", "conversation": str(convo.pk), "body": f"spam {i}"})
    kinds = [(await a.receive_json_from())["type"] for _ in range(3)]
    assert kinds.count("error") == 1
    await a.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_new_conversation_notifies_and_subscribes_open_sockets(alice, bob):
    from chat import realtime

    b = make_socket(bob)
    await b.connect()

    convo, _ = await sync_to_async(services.get_or_create_direct)(alice, bob)
    await sync_to_async(realtime.notify_conversation_added)(convo.pk, [bob.pk])
    event = await b.receive_json_from()
    assert event == {"type": "conversation.added", "conversation": str(convo.pk)}

    # Bob's already-open socket now receives messages in the brand-new conversation.
    a = make_socket(alice)
    await a.connect()
    await a.send_json_to({"type": "message", "conversation": str(convo.pk), "body": "welcome"})
    assert (await b.receive_json_from())["message"]["body"] == "welcome"

    await a.disconnect()
    await b.disconnect()


async def test_bad_conversation_id_is_an_error_not_a_crash(alice):
    a = make_socket(alice)
    await a.connect()
    await a.send_json_to({"type": "message", "conversation": "nope", "body": "x"})
    assert (await a.receive_json_from())["type"] == "error"
    await a.disconnect()


# ------------------------------------------- full ASGI stack (cookies, origin)
def _cookie_header(client):
    return f"sessionid={client.cookies['sessionid'].value}".encode()


@pytest.mark.django_db(transaction=True)
async def test_full_asgi_stack_authenticates_from_session_cookie(verified_client, alice, bob):
    from config.asgi import application

    convo, _ = await sync_to_async(services.get_or_create_direct)(alice, bob)
    headers = [(b"origin", b"http://localhost"), (b"host", b"localhost"), (b"cookie", _cookie_header(verified_client))]

    ok = WebsocketCommunicator(application, "/ws/chat/", headers=headers)
    connected, _ = await ok.connect()
    assert connected
    await ok.send_json_to({"type": "message", "conversation": str(convo.pk), "body": "via cookie"})
    assert (await ok.receive_json_from())["message"]["sender"]["id"] == alice.pk
    await ok.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_full_asgi_stack_rejects_anonymous_unverified_and_foreign_origin(client, verified_client, alice):
    from config.asgi import application

    # no cookie at all
    anon = WebsocketCommunicator(
        application, "/ws/chat/", headers=[(b"origin", b"http://localhost"), (b"host", b"localhost")]
    )
    assert not (await anon.connect())[0]

    # logged in but code not entered yet
    await sync_to_async(client.force_login)(alice)
    unverified = WebsocketCommunicator(
        application,
        "/ws/chat/",
        headers=[(b"origin", b"http://localhost"), (b"host", b"localhost"), (b"cookie", _cookie_header(client))],
    )
    assert not (await unverified.connect())[0]

    # valid session, but the page that opened the socket is on another site
    evil = WebsocketCommunicator(
        application,
        "/ws/chat/",
        headers=[(b"origin", b"http://evil.example"), (b"host", b"localhost"), (b"cookie", _cookie_header(verified_client))],
    )
    assert not (await evil.connect())[0]
