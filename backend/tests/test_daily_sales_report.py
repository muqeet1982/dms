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
    assert data["columns"] == ["date", "invoice", "customer", "sale_person", "payment_status", "total", "discount", "grand_total", "view"]
    assert data["column_labels"]["view"] == "View Invoice" and data["column_labels"]["invoice"] == "Invoice No"
    rows = {r["invoice"]: r for r in data["rows"]}
    ra, rb = rows[a["number"]], rows[b["number"]]
    assert (ra["sale_person"], ra["payment_status"], D(ra["total"]), D(ra["discount"]), D(ra["grand_total"])) == ("NAVEED", "Partially Paid", D("300"), D("30"), D("270"))
    assert (rb["sale_person"], rb["payment_status"], D(rb["grand_total"])) == ("No Sale Person", "Unpaid", D("150"))
    assert ra["invoice_id"] == a["id"] and data["links"]["view"] == {"to": "/app/sales/invoices/", "id": "invoice_id"}
    assert D(data["totals"]["grand_total"]) == D("420") and D(data["totals"]["discount"]) == D("30")
    assert "view" not in data["export_columns"], "no link column in the spreadsheet"
    assert [r["invoice"] for r in api.get("/api/v1/reports/daily_sales", {"rep": str(naveed.id)}).data["rows"]] == [a["number"]]
    assert [r["invoice"] for r in api.get("/api/v1/reports/daily_sales", {"payment_status": "Unpaid"}).data["rows"]] == [b["number"]]
    tomorrow = (timezone.localdate() + timezone.timedelta(days=1)).isoformat()
    assert api.get("/api/v1/reports/daily_sales", {"date_from": tomorrow, "date_to": tomorrow}).data["rows"] == []


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
