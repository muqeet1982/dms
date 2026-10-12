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
