from celery import shared_task


@shared_task
def purge_location_history():
    """
    Drop position fixes past each company's retention. Location history is employee data: keeping it longer than
    the company said it would is the kind of promise a product should keep without being asked.
    """
    from apps.core.tenancy import bypass_tenant, tenant_context
    from apps.platform.models import Tenant

    from .tracking import purge

    out = {}
    with bypass_tenant():
        for t in Tenant.objects.exclude(status__in=["cancelled", "archived"]):
            with tenant_context(t, t.owner):
                out[t.slug] = purge(t)["deleted"]
    return out


@shared_task
def sweep_shifts():
    """Every few minutes: catch dead trackers (a killed phone cannot report its own death) and close shifts
    left open past the daily cutoff. Only tenants whose tracking runs in shift mode are swept."""
    from apps.core.tenancy import bypass_tenant, tenant_context
    from apps.platform.models import Tenant

    from .shifts import sweep_tenant

    out = {}
    with bypass_tenant():
        for t in Tenant.objects.exclude(status__in=["cancelled", "archived"]):
            if t.setting("tracking_mode") != "shift" and not t.setting("require_shift_for_orders"):
                continue
            with tenant_context(t, t.owner):
                out[t.slug] = sweep_tenant(t)
    return out
