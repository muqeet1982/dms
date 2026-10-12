from celery import shared_task


@shared_task
def nightly_reconciliation():
    """Stock cache vs ledger, AR/AP control vs sub-ledger, rep clearing vs open collections, inventory value vs GL."""
    from decimal import Decimal

    from django.db.models import Sum

    from apps.core.notifications import notify
    from apps.core.tenancy import bypass_tenant, tenant_context
    from apps.platform.models import Tenant

    results = {}
    with bypass_tenant():
        tenants = list(Tenant.objects.exclude(status__in=["cancelled", "archived"]))
    for t in tenants:
        with tenant_context(t):
            from apps.accounting.models import JournalLine
            from apps.accounting.reports import inventory_reconciliation
            from apps.accounting.services import ReportQueries, account_for
            from apps.inventory.services import StockService
            from apps.sales.models import RepCollection

            issues = []
            try:
                drift = StockService.recompute_levels(t)
                if drift:
                    issues.append(f"{len(drift)} stock level(s) drifted")
                ar_control = ReportQueries.account_balance(account_for("ar"))
                ar_sub = JournalLine.objects.filter(account=account_for("ar"), entry__status="posted", partner_type="customer").aggregate(d=Sum("debit"), c=Sum("credit"))
                if abs(ar_control - ((ar_sub["d"] or 0) - (ar_sub["c"] or 0)) - account_for("ar").opening_balance) > Decimal("0.01"):
                    issues.append("AR control ≠ customer sub-ledger")
                rep_clearing = ReportQueries.account_balance(account_for("rep_clearing"))
                held = RepCollection.objects.filter(status="held_by_rep").aggregate(s=Sum("amount"))["s"] or 0
                if abs(rep_clearing - held) > Decimal("0.01"):
                    issues.append(f"Rep clearing {rep_clearing} ≠ open collections {held}")
                inv = inventory_reconciliation()
                if abs(Decimal(inv["totals"]["difference"])) > Decimal("1"):
                    issues.append(f"Inventory value ≠ GL by {inv['totals']['difference']}")
            except Exception as e:  # pragma: no cover
                issues.append(f"check failed: {e}")
            results[t.slug] = issues
            if issues and t.owner_id:
                notify(t.owner, "approval.pending", "Nightly reconciliation found issues", "; ".join(issues), tenant_id=t.id)
    return results
