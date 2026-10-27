from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter


class AccountAdapter(DefaultAccountAdapter):
    """Password sign-up is closed: accounts are created through Google only."""

    def is_open_for_signup(self, request):
        return False


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    def is_open_for_signup(self, request, sociallogin):
        return True

    def populate_user(self, request, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)
        if not user.display_name:
            full = " ".join(part for part in (data.get("first_name"), data.get("last_name")) if part)
            user.display_name = (data.get("name") or full or "")[:80]
        return user
