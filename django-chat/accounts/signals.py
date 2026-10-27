import logging

from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from . import two_factor

logger = logging.getLogger(__name__)


@receiver(user_logged_in)
def require_second_factor(sender, request, user, **kwargs):
    """
    Every fresh login (Google or admin) starts unverified and triggers an
    emailed code. `request` is None for logins made outside a request.
    """
    if request is None:
        return
    two_factor.mark_unverified(request.session)
    try:
        two_factor.issue_code(user)
    except Exception:  # email outage must not 500 the login; user can hit "resend"
        logger.exception("Could not send verification code to user %s", user.pk)
