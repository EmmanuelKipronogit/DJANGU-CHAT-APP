from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import two_factor
from .forms import VerifyCodeForm
from .two_factor import VerifyResult


def login_view(request):
    if request.user.is_authenticated:
        if two_factor.is_verified(request.session):
            return redirect("chat:home")
        return redirect("accounts:verify")
    return render(request, "accounts/login.html")


def _mask_email(email):
    local, _, domain = email.partition("@")
    return f"{local[:2]}{'*' * max(1, len(local) - 2)}@{domain}"


def _safe_next(request):
    target = request.POST.get("next") or request.GET.get("next") or ""
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return reverse("chat:home")


@login_required
def verify_view(request):
    if two_factor.is_verified(request.session):
        return redirect(_safe_next(request))

    form = VerifyCodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        result = two_factor.verify_code(request.user, form.cleaned_data["code"])
        if result is VerifyResult.OK:
            two_factor.mark_verified(request.session)
            # Rotate the session id now that the privilege level changed.
            request.session.cycle_key()
            return redirect(_safe_next(request))

        if result is VerifyResult.INVALID:
            left = two_factor.attempts_left(request.user)
            form.add_error("code", f"That code is incorrect. {left} attempt{'s' if left != 1 else ''} left.")
        elif result is VerifyResult.EXPIRED:
            form.add_error(None, "That code has expired. Request a new one below.")
        elif result is VerifyResult.LOCKED:
            form.add_error(None, "Too many incorrect attempts. Request a new code below.")
        else:
            form.add_error(None, "No active code. Request a new one below.")

    return render(
        request,
        "accounts/verify.html",
        {
            "form": form,
            "masked_email": _mask_email(request.user.email),
            "resend_wait": two_factor.seconds_until_resend(request.user),
            "next": _safe_next(request),
        },
    )


@login_required
@require_POST
def resend_view(request):
    if two_factor.is_verified(request.session):
        return redirect("chat:home")
    wait = two_factor.seconds_until_resend(request.user)
    if wait:
        messages.error(request, f"Please wait {wait}s before requesting another code.")
    else:
        try:
            two_factor.issue_code(request.user)
            messages.success(request, "A new code is on its way.")
        except Exception:
            messages.error(request, "We couldn't send the email. Please try again shortly.")
    return redirect("accounts:verify")


@require_POST
def logout_view(request):
    logout(request)
    return redirect("accounts:login")
