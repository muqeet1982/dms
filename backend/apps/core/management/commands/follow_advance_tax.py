"""
Charge the advance income tax (236G/236H) on the documents not yet on the books, for every customer of a tenant who
has a rate: orders not billed yet and draft invoices are re-totalled at the customer's rate (owner, 31 Oct 2026).
Posted invoices keep what they were charged. A customer saved with a new rate does this by itself; this is for the
documents raised before advance tax was charged at all.

Safe by default: prints the plan and rolls back unless --commit is given. Always runs in one transaction.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.tenancy import tenant_context
from apps.platform.models import Tenant


class _DryRun(Exception):
    pass


class Command(BaseCommand):
    help = "Re-total unbilled orders and draft invoices at their customers' advance income tax rates (dry-run unless --commit)."

    def add_arguments(self, parser):
        parser.add_argument("tenant", help="Tenant slug")
        parser.add_argument("--commit", action="store_true", help="Persist the changes (otherwise dry-run)")

    def handle(self, *args, **opts):
        from apps.partners.models import Customer
        from apps.sales.models import SalesInvoice, SalesOrder
        from apps.sales.services import OrderService

        tenant = Tenant.objects.filter(slug=opts["tenant"]).first()
        if tenant is None:
            raise CommandError(f"No tenant {opts['tenant']!r}.")
        with tenant_context(tenant, tenant.owner):
            try:
                with transaction.atomic():
                    for c in Customer.objects.filter(tenant_id=tenant.id, advance_income_tax_rate__gt=0):
                        before = {d.number: d.total for d in [*SalesOrder.objects.filter(tenant_id=tenant.id, customer=c),
                                                               *SalesInvoice.objects.filter(tenant_id=tenant.id, customer=c)]}
                        moved = OrderService.follow_customer_advance_tax(c)
                        after = {d.number: d for d in [*SalesOrder.objects.filter(tenant_id=tenant.id, number__in=moved),
                                                       *SalesInvoice.objects.filter(tenant_id=tenant.id, number__in=moved)]}
                        self.stdout.write(f"{c.name} ({c.advance_income_tax_rate}%): {len(moved)} document(s)")
                        for no in moved:
                            d = after[no]
                            self.stdout.write(f"  {no} ({d.status}): {before[no]:,.2f} → {d.total:,.2f} (advance tax {d.advance_tax_total:,.2f})")
                    if not opts["commit"]:
                        raise _DryRun()
            except _DryRun:
                self.stdout.write("DRY RUN — rolled back, nothing changed. Pass --commit to apply.")
                return
        self.stdout.write("APPLIED")
