"""
Owner, 24 Nov 2026: a Daily sale report (Date, Invoice No, Customer, Sale Person, Payment Status, Total, Discount, Grand
Total, View Invoice); a Customer Type filter on Sales by customer; and no "(staff)" in Sales by Sale Person when the
sale person is on the order or the trip rather than on the invoice.
"""
from decimal import Decimal as D

import pytest
from django.utils import timezone

from apps.core.tenancy import tenant_context

from .conftest import issue_order, seed_basic

pytestmark = pytest.mark.django_db


def _bill(api, wh, cust, v, qty, rep=None):
    body = {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True, "lines": [{"variant": str(v.id), "qty": qty}]}
    if rep is not None:
        body["rep"] = str(rep.id)
    o = api.post("/api/v1/sales-orders", body, format="json").data
    issue_order(api, o["id"])
    return api.post(f"/api/v1/sales-orders/{o['id']}/invoice", {"post": True}, format="json").data[0]


@pytest.fixture
def world(api, tenant):
    from apps.partners.models import Customer, SupplierRep

    wh, sup, v, shop = seed_basic(tenant)                    # T.P 150
    with tenant_context(tenant, tenant.owner):
        salon = Customer.objects.create(code="C-SAL", name="Shine Salon", type="salon", credit_limit=D("100000"))
        naveed = SupplierRep.objects.create(supplier=sup, code="R-1", name="NAVEED", phone="03001110000")
        shop.default_discount_pct = D("10")
        shop.save(update_fields=["default_discount_pct"])
    a = _bill(api, wh, shop, v, "2", rep=naveed)             # 300 less 10% = 270, NAVEED on the order only
    b = _bill(api, wh, salon, v, "1")                        # nobody
    from apps.accounting.services import PaymentService
    from apps.sales.models import SalesInvoice

    with tenant_context(tenant, tenant.owner):
        SalesInvoice.objects.filter(pk=a["id"]).update(rep=None)   # as TDN's October invoices: the rep only on the order
        PaymentService.record(direction="in", partner_type="customer", partner_id=str(shop.id), partner_name=shop.name, amount=D("100"), method="cash",
                              allocations=[{"document_type": "sales_invoice", "document_id": a["id"], "amount": D("100")}])
    return a, b, naveed, salon


def test_the_daily_sale_report(api, world):
    a, b, naveed, _salon = world
    spec = {r["key"]: r for r in api.get("/api/v1/reports").data}["daily_sales"]
    assert spec["title"] == "Daily sale report"
    data = api.get("/api/v1/reports/daily_sales").data                  # opens on today
    assert data["columns"] == ["date", "type", "invoice", "customer", "sale_person", "payment_status", "total", "discount", "discount_pct", "tax", "grand_total", "pdf"]
    assert data["column_labels"]["invoice"] == "Invoice / Return No"
    rows = {r["invoice"]: r for r in data["rows"]}
    ra, rb = rows[a["number"]], rows[b["number"]]
    assert (ra["type"], ra["sale_person"], ra["payment_status"], D(ra["total"]), D(ra["discount"]), D(ra["tax"]), D(ra["grand_total"])) == ("Sale", "NAVEED", "Partially Paid", D("300"), D("30"), D("0"), D("270"))
    assert (rb["sale_person"], rb["payment_status"], D(rb["grand_total"])) == ("No Sale Person", "Unpaid", D("150"))
    assert ra["href"] == f"/app/sales/invoices/{a['id']}?view=1" and data["links"] == {"invoice": {"href": "href"}}
    # owner, 26 Nov 2026: Discount % beside the Discount, and a PDF of the invoice where View was
    assert (ra["discount_pct"], rb["discount_pct"], data["column_labels"]["discount_pct"]) == ("10.00", "0", "Discount %")
    assert ra["pdf_url"] == f"/api/v1/invoices/{a['id']}/pdf" and data["downloads"] == {"pdf": {"url": "pdf_url"}}
    pdf = api.get(ra["pdf_url"])
    assert pdf.status_code == 200 and pdf["Content-Type"] == "application/pdf"
    for r in data["rows"]:
        assert D(r["total"]) - D(r["discount"]) + D(r["tax"]) == D(r["grand_total"]), r
    cards = {c["label"]: c for c in data["summary"]}
    assert D(cards["Sales"]["value"]) == D("420") and D(cards["Received"]["value"]) == D("100") and D(cards["Outstanding"]["value"]) == D("320")
    assert D(cards["Returns"]["value"]) == 0 and D(cards["Net sales"]["value"]) == D("420")
    assert D(data["totals"]["grand_total"]) == D("420") and D(data["totals"]["discount"]) == D("30")
    assert "pdf" not in data["export_columns"] and "href" not in data["export_columns"], "no link column in the spreadsheet"
    assert [r["invoice"] for r in api.get("/api/v1/reports/daily_sales", {"rep": str(naveed.id)}).data["rows"]] == [a["number"]]
    assert [r["invoice"] for r in api.get("/api/v1/reports/daily_sales", {"payment_status": "Unpaid"}).data["rows"]] == [b["number"]]
    tomorrow = (timezone.localdate() + timezone.timedelta(days=1)).isoformat()
    assert api.get("/api/v1/reports/daily_sales", {"date_from": tomorrow, "date_to": tomorrow}).data["rows"] == []


def test_the_daily_sale_report_shows_the_days_returns(api, tenant, world):
    """Owner, 24 Nov 2026: returns in the table and as a card — a credit note is a row in minus, the totals are the net."""
    from apps.sales.models import SalesInvoice
    from apps.sales.services import CreditNoteService

    _a, b, _naveed, _salon = world
    with tenant_context(tenant, tenant.owner):
        order_id = str(SalesInvoice.objects.get(pk=b["id"]).order_id)
    assert api.post(f"/api/v1/sales-orders/{order_id}/mark-delivered", {}, format="json").status_code == 200
    with tenant_context(tenant, tenant.owner):
        inv = SalesInvoice.objects.get(pk=b["id"])
        line = inv.lines.first()
        cn = CreditNoteService.create(customer=inv.customer, invoice=inv, reason="returned",
                                      lines=[{"variant": line.variant, "qty": "1", "unit_price": str(line.unit_price), "unit_cost": "100", "condition": "good", "restock": True}])
        CreditNoteService.post(cn)
    data = api.get("/api/v1/reports/daily_sales").data
    ret = [r for r in data["rows"] if r["type"] == "Return"]
    assert len(ret) == 1 and ret[0]["invoice"] == cn.number and D(ret[0]["grand_total"]) == D("-150"), ret
    assert ret[0]["payment_status"] == f"Return of {b['number']}" and ret[0]["href"] == f"/app/sales/credit-notes/{cn.pk}"
    pdf = api.get(ret[0]["pdf_url"])                                    # the return's own PDF (owner, 26 Nov 2026)
    assert ret[0]["pdf_url"] == f"/api/v1/credit-notes/{cn.pk}/pdf" and pdf.status_code == 200 and pdf["Content-Type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF-")
    cards = {c["label"]: c for c in data["summary"]}
    assert D(cards["Returns"]["value"]) == D("150") and cards["Returns"]["sub"] == "1 credit note" and D(cards["Net sales"]["value"]) == D("270")
    assert D(data["totals"]["grand_total"]) == D("270"), "sales less returns"
    assert {r["type"] for r in api.get("/api/v1/reports/daily_sales", {"show": "Returns only"}).data["rows"]} == {"Return"}
    assert {r["type"] for r in api.get("/api/v1/reports/daily_sales", {"show": "Sales only"}).data["rows"]} == {"Sale"}


def test_sales_by_sale_person_finds_the_rep_on_the_order(api, world):
    _a, _b, naveed, _salon = world
    labels = {r["label"]: r for r in api.get("/api/v1/reports/sales_by_rep").data["rows"]}
    assert "(staff)" not in labels and set(labels) == {"NAVEED", "No Sale Person"}
    assert labels["NAVEED"]["key"] == str(naveed.id), "the name still opens their daily sheet"


def test_sales_by_customer_by_customer_type(api, world):
    spec = {r["key"]: r for r in api.get("/api/v1/reports").data}["sales_by_customer"]
    assert any(p["name"] == "customer_type" for p in spec["params"])
    assert [r["label"] for r in api.get("/api/v1/reports/sales_by_customer", {"customer_type": "Salon"}).data["rows"]] == ["Shine Salon"]
    assert [r["label"] for r in api.get("/api/v1/reports/sales_by_customer", {"customer_type": "Retailer"}).data["rows"]] == ["Salon One"]
    assert len(api.get("/api/v1/reports/sales_by_customer").data["rows"]) == 2


def test_billed_adds_up_and_same_named_reps_are_told_apart(api, tenant, world):
    from apps.partners.models import SupplierRep

    _a, _b, naveed, _salon = world
    with tenant_context(tenant, tenant.owner):
        SupplierRep.objects.create(supplier=naveed.supplier, code="R-2", name="NAVEED", phone="03002220000")
    data = api.get("/api/v1/reports/sales_by_rep").data
    assert not {"billed", "credited"} & {*data["columns"], *data["totals"]}, "worked out, not shown (owner, 25 Nov 2026)"
    for r in data["rows"]:
        assert D(r["billed"]) == D(r["credited"]) + D(r["received"]) + D(r["outstanding"]), r
    assert {r["label"] for r in data["rows"]} == {"NAVEED (R-1)", "No Sale Person"}, "two of the same name carry their code"
    assert sum(D(r["billed"]) for r in data["rows"]) == D("420")


def test_sales_by_customer_takes_returns_off(api, tenant, world):
    """Owner, 24 Nov 2026: Sales by customer counted every sale in full — a customer who sent everything back still showed
    their sales. Returns (by credit-note date) come off: Returns, Net Sales, the returned cost off COGS."""
    from apps.sales.models import SalesInvoice
    from apps.sales.services import CreditNoteService

    _a, b, _naveed, salon = world
    with tenant_context(tenant, tenant.owner):
        order_id = str(SalesInvoice.objects.get(pk=b["id"]).order_id)
    assert api.post(f"/api/v1/sales-orders/{order_id}/mark-delivered", {}, format="json").status_code == 200
    with tenant_context(tenant, tenant.owner):
        inv = SalesInvoice.objects.get(pk=b["id"])
        line = inv.lines.first()
        cost = line.unit_cost
        CreditNoteService.post(CreditNoteService.create(customer=inv.customer, invoice=inv, reason="all back",
                                                        lines=[{"variant": line.variant, "qty": "1", "unit_price": str(line.unit_price), "unit_cost": str(cost), "condition": "good", "restock": True}]))
    data = api.get("/api/v1/reports/sales_by_customer").data
    assert data["columns"][:7] == ["label", "qty", "sales", "returns", "net_sales", "company_share", "cogs"]
    row = {r["label"]: r for r in data["rows"]}["Shine Salon"]
    assert (D(row["sales"]), D(row["returns"]), D(row["net_sales"]), D(row["qty"]), D(row["cogs"])) == (D("150"), D("150"), D("0"), D("0"), D("0")), row
    assert D(data["totals"]["net_sales"]) == D("270") and D(data["totals"]["returns"]) == D("150")
    salons = api.get("/api/v1/reports/sales_by_customer", {"customer_type": "Salon"}).data
    assert [r["label"] for r in salons["rows"]] == ["Shine Salon"] and D(salons["totals"]["returns"]) == D("150"), "the type filter reaches the returns too"
    assert api.get("/api/v1/reports/sales_by_customer", {"customer_type": "Retailer"}).data["totals"]["returns"] == "0"


def test_every_grouped_sales_report_takes_returns_off(api, tenant, world):
    """Owner, 24 Nov 2026 ("3 = yes fix"): by product, brand, supplier, territory and route — returns off, like by customer."""
    from apps.sales.models import SalesInvoice
    from apps.sales.services import CreditNoteService

    _a, b, _naveed, _salon = world
    with tenant_context(tenant, tenant.owner):
        order_id = str(SalesInvoice.objects.get(pk=b["id"]).order_id)
    assert api.post(f"/api/v1/sales-orders/{order_id}/mark-delivered", {}, format="json").status_code == 200
    with tenant_context(tenant, tenant.owner):
        inv = SalesInvoice.objects.get(pk=b["id"])
        line = inv.lines.first()
        CreditNoteService.post(CreditNoteService.create(customer=inv.customer, invoice=inv, reason="back",
                                                        lines=[{"variant": line.variant, "qty": "1", "unit_price": str(line.unit_price), "unit_cost": str(line.unit_cost), "condition": "good", "restock": True}]))
    for key in ("sales_by_product", "sales_by_brand", "sales_by_supplier", "sales_by_territory", "sales_by_route", "gross_margin_by_supplier"):
        data = api.get(f"/api/v1/reports/{key}").data
        t = data["totals"]
        assert data["columns"][2:5] == ["sales", "returns", "net_sales"], key
        assert (D(t["sales"]), D(t["returns"]), D(t["net_sales"])) == (D("420"), D("150"), D("270")), (key, t)
        assert D(t["gross_margin"]) == D(t["net_sales"]) - D(t["cogs"]), key
