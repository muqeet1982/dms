from celery import shared_task


@shared_task
def recompute_stock_levels():
    from apps.core.tenancy import bypass_tenant
    from apps.platform.models import Tenant

    from .services import StockService

    out = {}
    with bypass_tenant():
        for t in Tenant.objects.exclude(status__in=["cancelled", "archived"]):
            out[t.slug] = len(StockService.recompute_levels(t))
    return out


@shared_task
def compute_inventory_alerts():
    from apps.core.tenancy import bypass_tenant
    from apps.platform.models import Tenant

    from .services import AlertService

    out = {}
    with bypass_tenant():
        for t in Tenant.objects.exclude(status__in=["cancelled", "archived"]):
            out[t.slug] = AlertService.compute(t)
    return out
