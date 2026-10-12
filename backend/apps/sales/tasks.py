from celery import shared_task


@shared_task
def expire_reservations():
    from apps.core.tenancy import bypass_tenant, tenant_context
    from apps.platform.models import Tenant

    from .services import InvoiceService, OrderService, RepCollectionService

    out = {}
    with bypass_tenant():
        tenants = list(Tenant.objects.exclude(status__in=["cancelled", "archived"]))
    for t in tenants:
        with tenant_context(t):
            out[t.slug] = {"expired": OrderService.expire_reservations(), "overdue": InvoiceService.mark_overdue(), "backorders": OrderService.auto_cancel_backorders()}
            from apps.core.notifications import notify

            unsettled = list(RepCollectionService.unsettled_alerts())
            if unsettled and t.owner_id:
                total = sum((c.amount for c in unsettled), 0)
                notify(t.owner, "rep.settlement_due", f"{len(unsettled)} rep collection(s) unsettled", f"Total {total:.2f} held by reps beyond the alert window", tenant_id=t.id)
    return out
