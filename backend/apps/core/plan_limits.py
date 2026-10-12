"""Plan limit + feature flag enforcement (limits/features come from the tenant's plan, overridable per tenant)."""
from .exceptions import FeatureDisabledError, PlanLimitError


def effective_limits(tenant):
    sub = tenant.active_subscription
    limits = dict(sub.plan_snapshot.get("limits", {}) if sub else {})
    limits.update(tenant.limit_overrides or {})
    return limits


def effective_features(tenant):
    sub = tenant.active_subscription
    feats = dict(sub.plan_snapshot.get("features", {}) if sub else {})
    feats.update(tenant.feature_overrides or {})
    from apps.platform.models import FeatureFlag

    for flag in FeatureFlag.objects.all():
        if flag.enabled and (flag.all_tenants or flag.tenants.filter(pk=tenant.pk).exists()):
            feats[flag.key] = True
    return feats


class PlanLimitService:
    COUNTERS = {}

    @classmethod
    def register(cls, key, counter):
        cls.COUNTERS[key] = counter

    @classmethod
    def check(cls, tenant, key, extra=1):
        limits = effective_limits(tenant)
        limit = limits.get(key)
        if limit in (None, "", -1):
            return
        counter = cls.COUNTERS.get(key)
        if counter is None:
            return
        current = counter(tenant)
        if current + extra > int(limit):
            raise PlanLimitError(f"Plan limit reached for {key.replace('max_', '').replace('_', ' ')} ({current}/{limit}). Upgrade your plan or contact support.")

    @classmethod
    def require_feature(cls, tenant, key):
        if not effective_features(tenant).get(key):
            raise FeatureDisabledError(f"Feature '{key}' is not enabled for your plan.")


def feature_required(key):
    from functools import wraps

    def deco(fn):
        @wraps(fn)
        def wrapper(self, request, *args, **kwargs):
            PlanLimitService.require_feature(request.tenant, key)
            return fn(self, request, *args, **kwargs)

        return wrapper

    return deco


def platform_setting(key, default=None):
    """One project-level setting from the admin's Platform settings page (cached briefly), else the code default."""
    from django.core.cache import cache

    cached = cache.get("platform.settings")
    if cached is None:
        from apps.platform.models import PlatformSetting

        cached = PlatformSetting.get("global", {}) or {}
        cache.set("platform.settings", cached, 60)
    return cached.get(key, default)
