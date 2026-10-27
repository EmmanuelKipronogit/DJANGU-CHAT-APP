import pytest
from django.test import override_settings

from accounts.models import User


@pytest.fixture(autouse=True)
def _test_settings(settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
    settings.SECURE_SSL_REDIRECT = False
    settings.ALLOWED_HOSTS = ["testserver", "localhost"]
    settings.TWO_FACTOR_RESEND_COOLDOWN_SECONDS = 30
    settings.TWO_FACTOR_MAX_ATTEMPTS = 5
    settings.TWO_FACTOR_CODE_TTL_SECONDS = 600


@pytest.fixture
def alice(db):
    return User.objects.create_user("alice@example.com", display_name="Alice")


@pytest.fixture
def bob(db):
    return User.objects.create_user("bob@example.com", display_name="Bob")


@pytest.fixture
def carol(db):
    return User.objects.create_user("carol@example.com", display_name="Carol")


@pytest.fixture
def verified_client(client, alice):
    """A test client logged in as Alice with the second factor completed."""
    from accounts import two_factor

    client.force_login(alice)
    session = client.session
    two_factor.mark_verified(session)
    session.save()
    return client
