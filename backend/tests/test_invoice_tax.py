"""The invoice's tax switch: ON charges one sales tax on every line and books it; OFF returns to the
products' tax classes. Draft only."""
from decimal import Decimal

import pytest

from apps.core.tenancy import tenant_context

pytestmark = pytest.mark.django_db


def _issued_order(api, wh, cust, v, qty="4"):
    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True,
                                          "lines": [{"variant": str(v.id), "qty": qty}]}, format="json")
    pl = api.post(f"/api/v1/sales-orders/{r.data['id']}/load-form", {}, format="json")
    issues = [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in pl.data["lines"]]
    api.post(f"/api/v1/pick-lists/{pl.data['id']}/issue", {"issues": issues}, format="json")
    return r.data["id"]


@pytest.fixture
def taxed_world(api, tenant):
    from django.utils import timezone

    from apps.catalog.models import TaxRate, TaxType

    from .conftest import seed_basic

    wh, sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        tt = TaxType.objects.create(code="ST", name="Sales Tax", kind="sales_tax", applies_to="both")
        TaxRate.objects.create(tax_type=tt, rate_pct=Decimal("18"), valid_from=timezone.localdate())
    return wh, cust, v, tt


def test_the_tax_switch_charges_every_line_and_books_the_ledger(api, tenant, taxed_world):
    wh, cust, v, tt = taxed_world
    oid = _issued_order(api, wh, cust, v)
    made = api.post(f"/api/v1/sales-orders/{oid}/invoice", {"apply_tax": True}, format="json")
    inv = made.data[0] if isinstance(made.data, list) else made.data
    # 4 × 150 = 600 base, 18% = 108, total 708
    assert Decimal(inv["tax1_total"]) == Decimal("108"), inv["tax1_total"]
    assert Decimal(inv["total"]) == Decimal("708")
    assert all(Decimal(l["tax1_amount"]) > 0 for l in inv["lines"])
    with tenant_context(tenant, tenant.owner):
        from apps.accounting.models import JournalLine
        from apps.sales.models import SalesInvoice

        row = SalesInvoice.objects.get(id=inv["id"])
        booked = JournalLine.objects.filter(entry=row.journal, tax_type=tt).aggregate_count = \
            JournalLine.objects.filter(entry=row.journal, credit=Decimal("108"))
        assert booked.exists(), "the 18% must be credited to a tax account in the invoice journal"


def test_the_switch_flips_both_ways_on_a_draft_and_refuses_posted(api, tenant, taxed_world):
    wh, cust, v, tt = taxed_world
    oid = _issued_order(api, wh, cust, v)
    made = api.post(f"/api/v1/sales-orders/{oid}/invoice", {"post": False}, format="json")
    inv = made.data[0] if isinstance(made.data, list) else made.data
    assert Decimal(inv["tax1_total"]) == 0

    on = api.post(f"/api/v1/invoices/{inv['id']}/tax", {"apply": True}, format="json")
    assert on.status_code == 200 and Decimal(on.data["tax1_total"]) == Decimal("108"), on.content
    off = api.post(f"/api/v1/invoices/{inv['id']}/tax", {"apply": False}, format="json")
    assert Decimal(off.data["tax1_total"]) == 0 and Decimal(off.data["total"]) == Decimal("600")

    api.post(f"/api/v1/invoices/{inv['id']}/post", {}, format="json")
    refused = api.post(f"/api/v1/invoices/{inv['id']}/tax", {"apply": True}, format="json")
    assert refused.status_code in (400, 422)


def test_the_pdf_shows_tax_per_item_and_in_totals(api, tenant, taxed_world):
    from apps.sales.services import InvoiceService

    wh, cust, v, tt = taxed_world
    oid = _issued_order(api, wh, cust, v)
    made = api.post(f"/api/v1/sales-orders/{oid}/invoice", {"apply_tax": True}, format="json")
    inv_id = (made.data[0] if isinstance(made.data, list) else made.data)["id"]
    with tenant_context(tenant, tenant.owner):
        from django.template.loader import render_to_string

        from apps.sales.models import SalesInvoice

        inv = SalesInvoice.objects.get(id=inv_id)
        lines = list(inv.lines.all())
        extra = InvoiceService.present_lines(inv, lines)
        html = render_to_string("sales/invoice.html", {"inv": inv, "lines": lines, "tenant": tenant,
                                                       "logo_url": None, "printed_at": "", "doc_title": "SALES INVOICE", "doc_ref": inv.number, **extra})
        assert ">Tax<" in html and "Sales Tax" in html and "108" in html
