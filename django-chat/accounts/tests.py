from datetime import timedelta

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from accounts import two_factor
from accounts.models import TwoFactorCode
from accounts.two_factor import VerifyResult

pytestmark = pytest.mark.django_db


def test_issue_code_emails_code_and_stores_only_a_hash(alice):
    code = two_factor.issue_code(alice)

    assert len(code) == 6 and code.isdigit()
    assert len(mail.outbox) == 1
    assert code in mail.outbox[0].body
    assert mail.outbox[0].to == [alice.email]
    stored = TwoFactorCode.objects.get(user=alice)
    assert code not in stored.code_hash


def test_correct_code_verifies_once_only(alice):
    code = two_factor.issue_code(alice)
    assert two_factor.verify_code(alice, code) is VerifyResult.OK
    # consumed: replaying it fails
    assert two_factor.verify_code(alice, code) is VerifyResult.NO_CODE


def test_whitespace_in_code_is_tolerated(alice):
    code = two_factor.issue_code(alice)
    assert two_factor.verify_code(alice, f" {code[:3]} {code[3:]} ") is VerifyResult.OK


def test_wrong_code_then_lockout(alice, settings):
    settings.TWO_FACTOR_MAX_ATTEMPTS = 3
    code = two_factor.issue_code(alice)
    wrong = "000000" if code != "000000" else "111111"

    assert two_factor.verify_code(alice, wrong) is VerifyResult.INVALID
    assert two_factor.verify_code(alice, wrong) is VerifyResult.INVALID
    assert two_factor.verify_code(alice, wrong) is VerifyResult.LOCKED
    # even the right code is refused once locked
    assert two_factor.verify_code(alice, code) is VerifyResult.LOCKED


def test_expired_code_is_rejected(alice):
    code = two_factor.issue_code(alice)
    TwoFactorCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert two_factor.verify_code(alice, code) is VerifyResult.EXPIRED


def test_new_code_invalidates_the_old_one(alice):
    old = two_factor.issue_code(alice)
    new = two_factor.issue_code(alice)
    if old != new:
        assert two_factor.verify_code(alice, old) is VerifyResult.INVALID
    assert two_factor.verify_code(alice, new) is VerifyResult.OK


def test_resend_cooldown(alice):
    assert two_factor.seconds_until_resend(alice) == 0
    two_factor.issue_code(alice)
    assert 0 < two_factor.seconds_until_resend(alice) <= 30


# ----------------------------------------------------------------- the flow
def test_login_triggers_code_and_blocks_app_until_verified(client, alice):
    client.force_login(alice)  # fires user_logged_in, like a Google callback does
    assert len(mail.outbox) == 1

    response = client.get("/")
    assert response.status_code == 302
    assert response.url.startswith(reverse("accounts:verify"))

    # the JSON API answers 403 instead of redirecting
    api = client.get("/api/conversations/")
    assert api.status_code == 403


def test_full_verify_flow_unlocks_the_app(client, alice):
    client.force_login(alice)
    code = mail.outbox[0].body.split("code is ")[1][:6]

    bad = client.post(reverse("accounts:verify"), {"code": "abc"})
    assert bad.status_code == 200
    assert client.get("/").status_code == 302

    ok = client.post(reverse("accounts:verify"), {"code": code})
    assert ok.status_code == 302
    assert client.get("/").status_code == 200


def test_verify_page_requires_login(client):
    response = client.get(reverse("accounts:verify"))
    assert response.status_code == 302
    assert "/login/" in response.url


def test_resend_respects_cooldown(client, alice):
    client.force_login(alice)
    assert len(mail.outbox) == 1
    client.post(reverse("accounts:resend"))
    assert len(mail.outbox) == 1  # blocked by the cooldown

    TwoFactorCode.objects.update(created_at=timezone.now() - timedelta(minutes=5))
    client.post(reverse("accounts:resend"))
    assert len(mail.outbox) == 2


def test_open_redirect_is_not_followed(client, alice):
    client.force_login(alice)
    code = mail.outbox[0].body.split("code is ")[1][:6]
    response = client.post(reverse("accounts:verify"), {"code": code, "next": "https://evil.example/"})
    assert response.url == "/"


def test_login_page_renders_google_button(client):
    response = client.get(reverse("accounts:login"))
    assert response.status_code == 200
    assert b"Continue with Google" in response.content
