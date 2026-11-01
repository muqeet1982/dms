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


# ---------------------------------------------------------------- what each limit counts
# The plan's limits were checked against counters nobody had registered, so every check passed and no limit
# held (audit, 1 Nov 2026: "make sure all permission given to tenant are working, like user …"). Each counter
# counts that company's live rows only — the same figures its Subscription page shows as usage.

def _users(tenant):
    from apps.accounts.models import TenantMembership

    # office logins, invited ones included — an invitation takes the seat it will fill
    return TenantMembership.objects.filter(tenant=tenant, status__in=["active", "invited"], user__user_type="TENANT").count()


def _reps(tenant):
    from apps.partners.models import SupplierRep

    return SupplierRep.all_objects.filter(tenant=tenant, is_active=True).count()


def _warehouses(tenant):
    from apps.inventory.models import Warehouse

    # a vehicle's own stock location (two-step dispatch) is made by the system, not bought as a warehouse
    return Warehouse.all_objects.filter(tenant=tenant, is_active=True).exclude(type="van").count()


def _suppliers(tenant):
    from apps.partners.models import Supplier

    return Supplier.all_objects.filter(tenant=tenant, is_active=True).count()


def _products(tenant):
    from apps.catalog.models import ProductVariant

    # a deleted product is archived, not removed — it no longer takes a place on the plan
    return ProductVariant.all_objects.filter(tenant=tenant).exclude(product__status="archived").count()


def _orders_month(tenant):
    from django.utils import timezone

    from apps.sales.models import SalesOrder

    return SalesOrder.all_objects.filter(tenant=tenant, created_at__date__gte=timezone.localdate().replace(day=1)).count()


def _storage_mb(tenant):
    from django.db.models import Sum

    from apps.core.models import Attachment

    size = Attachment.all_objects.filter(tenant=tenant).aggregate(s=Sum("size"))["s"] or 0
    return int(size // (1024 * 1024))


def _custom_roles(tenant):
    from apps.accounts.models import GroupProfile
    from apps.accounts.services import DEFAULT_ROLES

    # the roles a company made itself: the system Owner role and the starting templates every company is given
    # do not use up the plan's allowance (a fresh company read 10/10 before it had made one)
    return GroupProfile.objects.filter(tenant=tenant, is_system=False).exclude(name__in=list(DEFAULT_ROLES)).count()


for _key, _counter in (("max_users", _users), ("max_reps", _reps), ("max_warehouses", _warehouses), ("max_suppliers", _suppliers),
                       ("max_products", _products), ("max_orders_month", _orders_month), ("max_storage_mb", _storage_mb),
                       ("max_custom_roles", _custom_roles)):
    PlanLimitService.register(_key, _counter)


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
