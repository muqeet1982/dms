"""FED, Extra Tax, Further Tax and WHT (owner, 26 Oct 2026: "missing fields in fbr section in add product").
Each is a % of the line's TAXABLE value set on the product's FBR card (INVOICE_TAX_FLOW_GUIDE §4). FED, Extra
and Further are added to the bill; WHT is the sales tax the buyer withholds, so it comes off what they owe —
Total Payable = Grand Total − WHT, which is exactly the totalValues the FBR filing carries."""
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

import pytest

from apps.core.tenancy import tenant_context

pytestmark = pytest.mark.django_db

D = Decimal


@pytest.fixture
def world(api, tenant):
    from django.utils import timezone

    from apps.catalog.models import Product, TaxRate, TaxType

    from .conftest import seed_basic

    wh, sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        tt = TaxType.objects.create(code="ST", name="Sales Tax", kind="sales_tax", applies_to="both")
        TaxRate.objects.create(tax_type=tt, rate_pct=D("18"), valid_from=timezone.localdate())
        Product.objects.filter(pk=v.product_id).update(
            fbr_rate_desc="18%", fbr_hs_code="7306.3090", fbr_sale_type="Goods at standard rate (default)",
            fbr_uom="Numbers, pieces, units", fbr_fed_rate=D("2"), fbr_extra_tax_rate=D("1"),
            fbr_further_tax_rate=D("3"), fbr_wht_rate=D("5"))
    return wh, cust, v, tt


def test_line_taxes_reproduce_the_guides_worked_example_f(tenant):
    """§15 F: 4 × 250, 10% discount, 18% GST, FED 2%, Further 3%, WHT 5% — every secondary tax on the
    taxable 900, never on taxable + GST."""
    from apps.sales.services import InvoiceService

    p = SimpleNamespace(fbr_rate_desc="18%", fbr_sale_type="", mrp_ex_tax=None, fbr_fed_rate=D("2"),
                        fbr_extra_tax_rate=D("0"), fbr_further_tax_rate=D("3"), fbr_wht_rate=D("5"))
    t = InvoiceService.line_taxes(p, qty=D("4"), unit_price=D("250"), discount_amount=D("100"))
    assert (t["gst"], t["fed"], t["extra"], t["further"], t["wht"]) == (D("162.00"), D("18.00"), D("0.00"), D("27.00"), D("45.00"))


def _issue(api, wh, cust, v, qty="4", charge_tax=True):
    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True, "charge_tax": charge_tax,
                                          "lines": [{"variant": str(v.id), "qty": qty}]}, format="json")
    assert r.status_code == 201, r.content
    pl = api.post(f"/api/v1/sales-orders/{r.data['id']}/load-form", {}, format="json")
    api.post(f"/api/v1/pick-lists/{pl.data['id']}/issue", {"issues": [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in pl.data["lines"]]}, format="json")
    return r.data


def test_the_four_taxes_flow_from_the_product_through_order_invoice_ledger_filing_and_return(api, tenant, world):
    wh, cust, v, tt = world
    # 4 × 150 = 600 taxable: GST 108, FED 12, Extra 6, Further 18 → Grand Total 744; WHT 30 → Total Payable 714
    order = _issue(api, wh, cust, v)
    assert (D(order["tax1_total"]), D(order["fed_total"]), D(order["extra_tax_total"]), D(order["further_tax_total"]),
            D(order["wht_total"])) == (D("108"), D("12"), D("6"), D("18"), D("30"))
    line = order["lines"][0]
    assert D(line["line_total"]) == D("744"), "FED/Extra/Further ride inside the line; WHT does not"
    assert D(order["total"]) == D("714"), "the order collects what the buyer pays: Grand Total less WHT"

    made = api.post(f"/api/v1/sales-orders/{order['id']}/invoice", {"apply_tax": True}, format="json")
    assert made.status_code == 201, made.content
    inv = made.data[0] if isinstance(made.data, list) else made.data
    assert D(inv["total"]) == D(order["total"]) == D("714"), "order and invoice must agree to the paisa"
    assert (D(inv["fed_total"]), D(inv["extra_tax_total"]), D(inv["further_tax_total"]), D(inv["wht_total"])) == (D("12"), D("6"), D("18"), D("30"))

    with tenant_context(tenant, tenant.owner):
        from apps.accounting.models import JournalLine
        from apps.accounting.services import account_for
        from apps.fbr.services import build_payload
        from apps.sales.models import SalesInvoice
        from apps.sales.services import CreditNoteService

        row = SalesInvoice.objects.get(id=inv["id"])
        jl = list(JournalLine.objects.filter(entry=row.journal))
        assert sum(l.debit for l in jl) == sum(l.credit for l in jl), "the invoice journal balances"
        ar = account_for("ar")
        assert sum(l.debit for l in jl if l.account_id == (cust.account_id or ar.id)) == D("714")
        # unconfigured roles fall back to Output Tax 2 (Further / Other Tax) and Withholding Tax Receivable
        assert sum(l.credit for l in jl if l.account_id == account_for("tax2_out").id) == D("36")
        assert sum(l.debit for l in jl if l.account_id == account_for("withholding_receivable").id) == D("30")

        filing = SimpleNamespace(seller_ntn_cnic="1234567", seller_business_name="Demo", seller_province="Punjab",
                                 seller_address="Lahore", buyer_ntn_cnic="", buyer_business_name=cust.name,
                                 buyer_province="Punjab", buyer_address="Lahore", buyer_registration_type="Unregistered")
        item = build_payload(row, filing, use_sandbox=False)["items"][0]
        assert (item["salesTaxApplicable"], item["fedPayable"], item["extraTax"], item["furtherTax"], item["salesTaxWithheldAtSource"]) == (108, 12, 6, 18, 30)
        assert D(str(item["totalValues"])) == row.total, "the filed totalValues IS the invoice's Total Payable"

        cn = CreditNoteService.create(customer=cust, invoice=row, warehouse=wh,
                                      lines=[{"variant": v, "qty": D("4"), "unit_price": D("150")}])
        assert (cn.fed_total, cn.extra_tax_total, cn.further_tax_total, cn.wht_total) == (D("12"), D("6"), D("18"), D("30"))
        assert cn.total == D("714"), "a full return credits back exactly what the buyer owed"
        CreditNoteService.post(cn)
        cl = list(JournalLine.objects.filter(entry=cn.journal))
        assert sum(l.debit for l in cl) == sum(l.credit for l in cl), "the credit note journal balances"


def test_the_print_shows_other_tax_and_the_wht_deduction(api, tenant, world):
    wh, cust, v, tt = world
    order = _issue(api, wh, cust, v)
    inv = api.post(f"/api/v1/sales-orders/{order['id']}/invoice", {"apply_tax": True}, format="json").data
    inv = inv[0] if isinstance(inv, list) else inv
    seen = {}

    def capture(request, template, ctx, *a, **kw):
        from django.http import HttpResponse
        from django.template.loader import render_to_string

        seen["html"] = render_to_string(template, {**ctx, "tenant": tenant, "logo_url": None, "printed_at": "",
                                                   "doc_title": kw.get("title", ""), "doc_ref": ""})
        seen.update(ctx)
        return HttpResponse(b"")

    with mock.patch("apps.sales.documents.render_doc", side_effect=capture):
        assert api.get(f"/api/v1/invoices/{inv['id']}/pdf").status_code == 200
    html = seen["html"]
    assert ">Other Tax<" in html and "FED 2% · Extra 1% · Further 3%" in html
    assert seen["total_tax"] == D("144") and seen["grand_total"] == D("744") and seen["wht_total"] == D("30")
    for row in ("FED", "Extra Tax", "Further Tax", "Less: WHT"):
        assert row in html, row

    with mock.patch("apps.sales.documents.render_doc", side_effect=capture):
        assert api.get(f"/api/v1/sales-orders/{order['id']}/pdf").status_code == 200
    assert ">Other Tax<" in seen["html"] and "Less: WHT" in seen["html"]
    assert seen["grand_total"] == D("744")


def test_untaxed_documents_charge_none_of_them(api, tenant, world):
    """The rates sit on the product but only a tax document charges them — a plain order stays plain."""
    wh, cust, v, tt = world
    order = _issue(api, wh, cust, v, charge_tax=False)
    assert all(D(order[k]) == 0 for k in ("fed_total", "extra_tax_total", "further_tax_total", "wht_total"))
    made = api.post(f"/api/v1/sales-orders/{order['id']}/invoice", {"apply_tax": False}, format="json")
    inv = made.data[0] if isinstance(made.data, list) else made.data
    assert D(inv["total"]) == D("600") and D(inv["wht_total"]) == 0


def test_the_product_form_saves_the_rates_and_refuses_nonsense(api, tenant, world):
    wh, cust, v, tt = world
    pid = str(v.product_id)
    r = api.patch(f"/api/v1/products/{pid}", {"fbr_fed_rate": "2.5", "fbr_wht_rate": "1"}, format="json")
    assert r.status_code == 200, r.content
    assert D(r.data["fbr_fed_rate"]) == D("2.5") and D(r.data["fbr_wht_rate"]) == D("1")
    bad = api.patch(f"/api/v1/products/{pid}", {"fbr_further_tax_rate": "150"}, format="json")
    assert bad.status_code == 400 and "Further tax" in str(bad.content)
