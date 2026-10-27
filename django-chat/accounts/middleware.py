from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse

from . import two_factor

# Paths an authenticated-but-unverified user may still reach.
EXEMPT_PREFIXES = ("/verify/", "/logout/", "/static/", "/healthz")


class TwoFactorMiddleware:
    """Sends logged-in users who haven't entered their emailed code to /verify/."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (
            user is not None
            and user.is_authenticated
            and not two_factor.is_verified(request.session)
            and not request.path.startswith(EXEMPT_PREFIXES)
        ):
            if request.path.startswith("/api/"):
                return JsonResponse({"error": "verification_required"}, status=403)
            target = reverse("accounts:verify")
            if request.method == "GET" and request.path != "/":
                target += f"?next={request.path}"
            return redirect(target)
        return self.get_response(request)
