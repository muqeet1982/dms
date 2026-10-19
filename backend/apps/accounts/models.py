import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractUser, Group
from django.db import models
from django.utils import timezone

from apps.core.uuid7 import uuid7


class User(AbstractUser):
    USER_TYPES = [("PLATFORM_ADMIN", "Platform admin"), ("TENANT", "Tenant user"), ("EXTERNAL", "External user")]

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    email = models.EmailField(unique=True, null=True, blank=True)
    phone = models.CharField(max_length=20, unique=True, null=True, blank=True, help_text="E.164")
    full_name = models.CharField(max_length=150, blank=True)
    user_type = models.CharField(max_length=20, choices=USER_TYPES, default="TENANT")
    mfa_enabled = models.BooleanField(default=False)
    last_login_ip = models.GenericIPAddressField(null=True, blank=True)
    locale = models.CharField(max_length=10, default="en")
    timezone = models.CharField(max_length=50, default="Asia/Karachi")
    avatar = models.ImageField(upload_to="avatars/", null=True, blank=True)
    must_change_password = models.BooleanField(default=False)
    failed_login_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    accepted_terms_version = models.CharField(max_length=20, blank=True)

    # username kept for AbstractUser compatibility; we log in with email or phone
    REQUIRED_FIELDS = ["email"]

    class Meta:
        ordering = ["full_name"]

    def __str__(self):
        return self.full_name or self.email or self.phone or str(self.id)

    @property
    def is_platform_admin(self):
        return self.user_type == "PLATFORM_ADMIN"

    def save(self, *args, **kwargs):
        # one address = one account: stored lower-case so "A@x.pk" and "a@x.pk" cannot both exist
        if self.email:
            self.email = self.email.strip().lower()
        if not self.username:
            self.username = self.email or self.phone or str(self.id)
        if self.email == "":
            self.email = None
        if self.phone == "":
            self.phone = None
        super().save(*args, **kwargs)

    def platform_perms(self):
        if not self.is_platform_admin:
            return set()
        if self.is_superuser:
            from .permissions_catalog import PLATFORM_CATALOG

            return {f"platform.{c}" for _, perms in PLATFORM_CATALOG.values() for c, _ in perms}
        return set(self.groups.values_list("permissions__codename", flat=True).exclude(permissions__codename=None))


class GroupProfile(models.Model):
    """Extends Django Group with tenant scope and role type."""

    ROLE_TYPES = [("internal", "Internal"), ("external", "External (rep)"), ("platform", "Platform")]
    group = models.OneToOneField(Group, on_delete=models.CASCADE, related_name="profile")
    tenant = models.ForeignKey("platform.Tenant", null=True, blank=True, on_delete=models.CASCADE, related_name="roles")
    name = models.CharField(max_length=80)
    description = models.CharField(max_length=200, blank=True)
    is_system = models.BooleanField(default=False)
    role_type = models.CharField(max_length=10, choices=ROLE_TYPES, default="internal")
    approval_limits = models.JSONField(default=dict, blank=True)  # {"stock_adjustment": "50000", "expense": "20000"}
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("tenant", "name")]

    def __str__(self):
        return self.name

    @property
    def permission_codenames(self):
        return sorted(self.group.permissions.values_list("codename", flat=True))


class TenantMembership(models.Model):
    STATUS = [("active", "Active"), ("suspended", "Suspended"), ("invited", "Invited")]
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    tenant = models.ForeignKey("platform.Tenant", on_delete=models.CASCADE, related_name="memberships")
    is_owner = models.BooleanField(default=False)
    status = models.CharField(max_length=10, choices=STATUS, default="active")
    groups = models.ManyToManyField(Group, blank=True, related_name="memberships")
    supplier_rep = models.OneToOneField("partners.SupplierRep", null=True, blank=True, on_delete=models.SET_NULL, related_name="membership")
    # where this login may sign in: the mobile app, the web, or both (the owner sets it per user)
    LOGIN_SURFACES = [("both", "App and web"), ("app", "Mobile app only"), ("web", "Web only")]
    login_surface = models.CharField(max_length=4, choices=LOGIN_SURFACES, default="both")
    joined_at = models.DateTimeField(auto_now_add=True)
    permissions_version = models.PositiveIntegerField(default=1)

    class Meta:
        unique_together = [("user", "tenant")]

    def __str__(self):
        return f"{self.user} @ {self.tenant}"

    def permission_codenames(self):
        from .page_permissions import all_page_codenames, expand

        if self.is_owner:
            from .permissions_catalog import all_codenames

            return set(all_codenames(include_external=False)) | set(all_page_codenames())
        stored = set(self.groups.values_list("permissions__codename", flat=True).exclude(permissions__codename=None))
        # a role saved before the page matrix holds coarse codenames — grant what they imply
        return expand(stored)

    def has_perm(self, codename):
        if self.status != "active":
            return False
        if self.is_owner:
            return not codename.startswith("external.") and not codename.startswith("platform.")
        return codename in self.permission_codenames()

    def approval_limit(self, key):
        """Highest approval limit across roles; None = unlimited (owner)."""
        if self.is_owner:
            return None
        best = None
        for g in self.groups.select_related("profile"):
            v = (getattr(g, "profile", None) and g.profile.approval_limits.get(key)) or None
            if v is not None:
                best = max(best or 0, float(v))
        return best


class UserWarehouseAccess(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    membership = models.ForeignKey(TenantMembership, on_delete=models.CASCADE, related_name="warehouse_access")
    warehouse = models.ForeignKey("inventory.Warehouse", on_delete=models.CASCADE, related_name="+")

    class Meta:
        unique_together = [("membership", "warehouse")]


class OneTimeCode(models.Model):
    PURPOSES = [("password_reset", "Password reset"), ("invite", "Invitation"), ("mfa", "MFA"), ("verify", "Verify contact")]
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="otps")
    purpose = models.CharField(max_length=20, choices=PURPOSES)
    code = models.CharField(max_length=8)
    channel = models.CharField(max_length=10, default="email")
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def issue(cls, user, purpose, channel="email", ttl_minutes=10):
        cls.objects.filter(user=user, purpose=purpose, used_at__isnull=True).update(used_at=timezone.now())
        return cls.objects.create(
            user=user, purpose=purpose, channel=channel, code=f"{secrets.randbelow(10**6):06d}", expires_at=timezone.now() + timedelta(minutes=ttl_minutes)
        )

    def is_valid(self):
        return self.used_at is None and self.expires_at > timezone.now() and self.attempts < 5


class UserSession(models.Model):
    """One row per refresh-token family (device). Revoking blacklists the current refresh token."""

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sessions")
    family = models.CharField(max_length=64, db_index=True)
    refresh_jti = models.CharField(max_length=64, db_index=True)
    # The token this one replaced: a second request that was already in flight with it (another tab, a retry)
    # is a race, not an attack, so it is honoured for DMS["REFRESH_GRACE_SECONDS"] instead of killing the family.
    prev_refresh_jti = models.CharField(max_length=64, blank=True, db_index=True)
    rotated_at = models.DateTimeField(null=True, blank=True)
    device = models.CharField(max_length=200, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    platform = models.CharField(max_length=20, default="web")
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(auto_now=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-last_used_at"]


class ImpersonationSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    admin = models.ForeignKey(User, on_delete=models.CASCADE, related_name="impersonations")
    target = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    tenant = models.ForeignKey("platform.Tenant", on_delete=models.CASCADE, related_name="+")
    started_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    reason = models.CharField(max_length=200, blank=True)


class QrLoginToken(models.Model):
    """
    "Log in on the phone" — the web app shows a QR code; scanning it with the mobile app signs that phone in without a
    password. Codes are random, stored hashed, single-use and short-lived (DMS["QR_LOGIN_TTL_SECONDS"]).
    """

    id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="qr_login_tokens")
    tenant = models.ForeignKey("platform.Tenant", null=True, blank=True, on_delete=models.SET_NULL, related_name="+", help_text="Company the user was working in when the code was made (hint for the app)")
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_ip = models.GenericIPAddressField(null=True, blank=True)
    created_device = models.CharField(max_length=200, blank=True)
    redeemed_ip = models.GenericIPAddressField(null=True, blank=True)
    redeemed_device = models.CharField(max_length=200, blank=True)
    redeemed_platform = models.CharField(max_length=20, blank=True)
    session = models.ForeignKey(UserSession, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]

    @staticmethod
    def hash(raw: str) -> str:
        return hashlib.sha256(raw.encode()).hexdigest()

    @classmethod
    def issue(cls, user, tenant=None, request=None, ttl_seconds=None):
        """Create a fresh code (invalidating the user's other pending codes). Returns (row, raw_token)."""
        now = timezone.now()
        cls.objects.filter(user=user, used_at__isnull=True, expires_at__gt=now).update(expires_at=now)
        raw = secrets.token_urlsafe(32)
        from apps.core.plan_limits import platform_setting

        ttl = ttl_seconds or platform_setting("qr_login_ttl_seconds", settings.DMS.get("QR_LOGIN_TTL_SECONDS", 300))
        row = cls.objects.create(user=user, tenant=tenant, token_hash=cls.hash(raw), expires_at=now + timedelta(seconds=ttl), created_ip=getattr(request, "client_ip", None), created_device=(getattr(request, "user_agent", "") or "")[:200])
        return row, raw

    @property
    def status(self) -> str:
        if self.used_at:
            return "used"
        return "expired" if self.expires_at <= timezone.now() else "pending"

    @classmethod
    def find(cls, raw: str):
        return cls.objects.select_related("user", "tenant").filter(token_hash=cls.hash((raw or "").strip())).first()
