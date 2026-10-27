from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra):
        if not email:
            raise ValueError("An email address is required.")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        return self._create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    """Email-identified user. Google sign-in creates these automatically."""

    email = models.EmailField(unique=True)
    display_name = models.CharField(max_length=80, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        ordering = ["display_name", "email"]

    def __str__(self):
        return self.email

    @property
    def name(self):
        """Best human-readable label for this user."""
        return self.display_name or self.email.split("@")[0]

    def get_full_name(self):
        return self.name

    def get_short_name(self):
        return self.name


class TwoFactorCode(models.Model):
    """
    A one-time verification code emailed after sign-in.

    Only an HMAC of the code is stored, never the code itself.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="two_factor_codes")
    code_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["user", "-created_at"])]
        ordering = ["-created_at"]

    def __str__(self):
        return f"2FA code for {self.user_id} ({self.created_at:%Y-%m-%d %H:%M})"
