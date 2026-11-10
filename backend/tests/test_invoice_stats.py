"""
The cards above the invoice list (owner, 10 Nov 2026: "count is not correct"): they count the invoices the table shows,
with its search and every filter, and a draft is not a sale yet.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.core.tenancy import tenant_context

from .conftest import seed_basic

pytestmark = pytest.mark.django_db
D = Decimal


def _invoice(cust, v, total, *, post=True, days_ago=0, due_in=30):
    from apps.sales.models import SalesInvoice, SalesInvoiceLine
    from apps.sales.services import InvoiceService

    day = timezone.localdate() - timedelta(days=days_ago)
    inv = SalesInvoice.objects.create(number=f"INV-ST-{SalesInvoice.objects.count() + 1}", customer=cust, date=day, due_date=day + timedelta(days=due_in),
                                      subtotal=D(total), total=D(total), status="draft")
    SalesInvoiceLine.objects.create(invoice=inv, variant=v, description=v.name, qty=D("1"), unit_price=D(total), unit_cost=D("1"), line_total=D(total))
    return InvoiceService.post(inv) if post else inv


def test_the_cards_follow_the_tables_filters(api, tenant):
    from apps.partners.models import Customer

    wh, sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        other = Customer.objects.create(code="C-2", name="Second Shop", credit_limit=D("100000"))
        _invoice(cust, v, "100")
        _invoice(cust, v, "200", days_ago=40, due_in=10)          # overdue
        _invoice(other, v, "300")
        _invoice(other, v, "999", post=False)                     # a draft: not a sale yet
    s = api.get("/api/v1/invoices/stats", {"tax": 0}).data
    assert s["open_count"] == 3 and D(s["outstanding"]) == 600 and D(s["overdue"]) == 200 and s["overdue_count"] == 1
    assert D(s["month_sales"]) == 400 and s["period"] is False, "the draft's 999 is not a sale; the overdue one is last month's"

    s = api.get("/api/v1/invoices/stats", {"tax": 0, "customer": str(other.id)}).data
    assert s["open_count"] == 1 and D(s["outstanding"]) == 300 and s["overdue_count"] == 0, "one customer picked: their invoices only"

    s = api.get("/api/v1/invoices/stats", {"tax": 0, "search": "Second"}).data
    assert s["open_count"] == 1 and D(s["outstanding"]) == 300

    s = api.get("/api/v1/invoices/stats", {"tax": 0, "status": "overdue"}).data
    assert s["open_count"] == api.get("/api/v1/invoices", {"tax": 0, "status": "overdue"}).data["count"]

    old = (timezone.localdate() - timedelta(days=45)).isoformat()
    s = api.get("/api/v1/invoices/stats", {"tax": 0, "date_from": old, "date_to": (timezone.localdate() - timedelta(days=35)).isoformat()}).data
    assert s["period"] is True and D(s["month_sales"]) == 200 and s["open_count"] == 1, "a date range: that period's sales"

    assert api.get("/api/v1/invoices/stats", {"tax": 1}).data["open_count"] == 0, "the tax invoices page counts its own"
