"""X-API-Key authentication for machine clients (§9.3 option 2)."""
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication

from apps.core.tenancy import bypass_tenant, set_current_tenant, set_current_user

from .models import ApiKey


class ApiKeyUser:
    """Minimal user-like object for API key requests."""

    is_authenticated = True
    is_active = True
    is_platform_admin = False
    user_type = "API"
    is_anonymous = False

    def __init__(self, api_key):
        self.api_key = api_key
        self.pk = self.id = api_key.id
        self.full_name = f"API key: {api_key.name}"
        self.email = None
        self.phone = None

    def __str__(self):
        return self.full_name

    def platform_perms(self):
        return set()


class ApiKeyAuthentication(BaseAuthentication):
    def authenticate(self, request):
        raw = request.headers.get("X-API-Key")
        if not raw:
            return None
        with bypass_tenant():
            key = ApiKey.all_objects.select_related("tenant", "supplier", "rep").filter(hashed_key=ApiKey.hash(raw), deleted_at__isnull=True).first()
        if key is None or not key.is_valid:
            raise exceptions.AuthenticationFailed("Invalid or expired API key.")
        ip = getattr(request, "client_ip", None)
        if key.ip_allowlist and ip not in key.ip_allowlist:
            raise exceptions.AuthenticationFailed("IP address not allowed for this key.")
        if key.tenant.status in ("suspended", "cancelled", "archived"):
            raise exceptions.AuthenticationFailed("Tenant account is not active.")
        from apps.core.plan_limits import effective_features

        if not effective_features(key.tenant).get("external_api"):
            raise exceptions.AuthenticationFailed("External API is not enabled for this plan.")
        # simple per-key rate limit (requests/minute) via cache
        from django.core.cache import cache

        bucket = f"apikey:{key.pk}:{timezone.now().strftime('%Y%m%d%H%M')}"
        n = cache.incr(bucket) if cache.get(bucket) is not None else (cache.set(bucket, 1, 70) or 1)
        if n > key.rate_limit:
            raise exceptions.Throttled(detail="API key rate limit exceeded.")
        ApiKey.all_objects.filter(pk=key.pk).update(last_used_at=timezone.now())
        request.tenant = key.tenant
        request.api_key = key
        request.membership = None
        request.impersonating = False
        user = ApiKeyUser(key)
        set_current_tenant(key.tenant)
        set_current_user(None)
        return user, key
