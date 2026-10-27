"""
Emailed one-time verification codes (the second step after Google sign-in).

Flow:
  1. A user logs in (Google, or the admin login) -> `issue_code()` emails a code
     and the session is marked as *not yet verified*.
  2. The user enters the code on /verify/ -> `verify_code()` -> session marked
     verified.
  3. `TwoFactorMiddleware` (HTTP) and the chat consumer (WebSocket) refuse
     anyone whose session is not verified.
"""
import enum
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from .models import TwoFactorCode

SESSION_KEY = "two_factor_verified"
CODE_LENGTH = 6


class VerifyResult(enum.Enum):
    OK = "ok"
    INVALID = "invalid"
    EXPIRED = "expired"
    LOCKED = "locked"  # too many wrong attempts
    NO_CODE = "no_code"


def _hash(user_id, code):
    return salted_hmac("accounts.two_factor", f"{user_id}:{code}").hexdigest()


def _generate_code():
    return f"{secrets.randbelow(10**CODE_LENGTH):0{CODE_LENGTH}d}"


def is_verified(session):
    return bool(session.get(SESSION_KEY))


def mark_verified(session):
    session[SESSION_KEY] = True


def mark_unverified(session):
    session[SESSION_KEY] = False


def seconds_until_resend(user):
    """0 when a new code may be requested, else the seconds left to wait."""
    last = user.two_factor_codes.order_by("-created_at").first()
    if last is None:
        return 0
    ready_at = last.created_at + timedelta(seconds=settings.TWO_FACTOR_RESEND_COOLDOWN_SECONDS)
    remaining = (ready_at - timezone.now()).total_seconds()
    return max(0, int(remaining) + (1 if remaining % 1 else 0))


@transaction.atomic
def issue_code(user):
    """Create a fresh code (invalidating older ones), email it, return the plain code."""
    now = timezone.now()
    user.two_factor_codes.filter(consumed_at__isnull=True).update(consumed_at=now)

    code = _generate_code()
    TwoFactorCode.objects.create(
        user=user,
        code_hash=_hash(user.pk, code),
        created_at=now,
        expires_at=now + timedelta(seconds=settings.TWO_FACTOR_CODE_TTL_SECONDS),
    )

    minutes = max(1, settings.TWO_FACTOR_CODE_TTL_SECONDS // 60)
    send_mail(
        subject=f"Your verification code: {code}",
        message=(
            f"Your verification code is {code}.\n\n"
            f"It expires in {minutes} minute{'s' if minutes != 1 else ''}. "
            "If you didn't try to sign in, you can ignore this email."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
    )
    return code


def verify_code(user, submitted):
    """Check a submitted code against the user's latest unused code."""
    submitted = "".join(ch for ch in (submitted or "") if ch.isdigit())

    with transaction.atomic():
        record = (
            user.two_factor_codes.select_for_update()
            .filter(consumed_at__isnull=True)
            .order_by("-created_at")
            .first()
        )
        if record is None:
            return VerifyResult.NO_CODE
        if record.expires_at <= timezone.now():
            return VerifyResult.EXPIRED
        if record.attempts >= settings.TWO_FACTOR_MAX_ATTEMPTS:
            return VerifyResult.LOCKED

        if len(submitted) == CODE_LENGTH and constant_time_compare(
            record.code_hash, _hash(user.pk, submitted)
        ):
            record.consumed_at = timezone.now()
            record.save(update_fields=["consumed_at"])
            return VerifyResult.OK

        record.attempts += 1
        record.save(update_fields=["attempts"])
        if record.attempts >= settings.TWO_FACTOR_MAX_ATTEMPTS:
            return VerifyResult.LOCKED
        return VerifyResult.INVALID


def attempts_left(user):
    record = user.two_factor_codes.filter(consumed_at__isnull=True).order_by("-created_at").first()
    if record is None:
        return 0
    return max(0, settings.TWO_FACTOR_MAX_ATTEMPTS - record.attempts)
