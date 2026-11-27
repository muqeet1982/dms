"""Owner, 27 Nov 2026: "need another report, an item not sold in last 30 days" and "also need product by sale person"."""
from decimal import Decimal as D

import pytest
from django.utils import timezone

from apps.core.tenancy import tenant_context

from .conftest import issue_order, seed_basic

pytestmark = pytest.mark.django_db


def _product(tenant, wh, sku, name, stock="0", cost="100"):
    from apps.catalog.models import UnitOfMeasure
    from apps.catalog.services import ProductService
    from apps.inventory.services import StockService

    with tenant_context(tenant, tenant.owner):
        v = ProductService.create_product(tenant, {"sku": sku, "name": name, "base_uom": UnitOfMeasure.objects.get(code="PC"), "cost_price": D(cost), "sale_price": D("150")}).variants.first()
        if D(stock):
            StockService.move(warehouse=wh, variant=v, qty=D(stock), movement_type="opening", unit_cost=D(cost))
        return v


def _sell(api, wh, cust, rep, lines):
    body = {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True, "lines": [{"variant": str(v.id), "qty": q} for v, q in lines]}
    if rep is not None:
        body["rep"] = str(rep.id)
    o = api.post("/api/v1/sales-orders", body, format="json").data
    issue_order(api, o["id"])
    return api.post(f"/api/v1/sales-orders/{o['id']}/invoice", {"post": True}, format="json").data[0]


def test_products_by_sale_person(api, tenant):
    from apps.partners.models import SupplierRep

    wh, sup, v, cust = seed_basic(tenant)
    shade = _product(tenant, wh, "62613UC", "NO. 6.13 - 60 ML", stock="20")
    with tenant_context(tenant, tenant.owner):
        naveed = SupplierRep.objects.create(supplier=sup, code="R-1", name="NAVEED", phone="03001110000")
        anza = SupplierRep.objects.create(supplier=sup, code="R-2", name="Anza", phone="03001110001")
    _sell(api, wh, cust, naveed, [(v, "2"), (shade, "3")])
    _sell(api, wh, cust, anza, [(shade, "1")])
    data = api.get("/api/v1/reports/product_by_rep").data
    assert data["columns"] == ["sale_person", "label", "qty", "sales", "returns", "net_sales", "cogs", "gross_margin", "margin_pct", "invoices"]
    assert "company_share" not in data["totals"] and data["column_labels"]["label"] == "Product Name", "no Company Share (owner, 27 Nov 2026)"
    assert data["totals"]["gross_margin"] == api.get("/api/v1/reports/sales_by_rep").data["totals"]["gross_margin"], "the margin still counts it"
    got = {(r["sale_person"], r["label"]): D(r["qty"]) for r in data["rows"]}
    assert got == {("Anza", shade.name): 1, ("NAVEED", v.name): 2, ("NAVEED", shade.name): 3}
    assert data["totals"]["net_sales"] == api.get("/api/v1/reports/sales_by_rep").data["totals"]["net_sales"], "adds up to Sales by Sale Person"
    one = api.get("/api/v1/reports/product_by_rep", {"rep": str(anza.id)}).data
    assert [(r["sale_person"], r["label"]) for r in one["rows"]] == [("Anza", shade.name)]
    spec = {r["key"]: r for r in api.get("/api/v1/reports").data}["product_by_rep"]
    assert spec["opens_on"] == "today" and spec["pick_columns"], "opens on today (owner, 27 Nov 2026)"
    for fmt in ("csv", "xlsx", "pdf"):
        assert api.get("/api/v1/reports/product_by_rep", {"format": fmt}).status_code == 200


def test_items_not_sold(api, tenant):
    from apps.sales.models import SalesInvoice

    wh, _sup, v, cust = seed_basic(tenant)                                   # v sells today
    idle = _product(tenant, wh, "IDLE-1", "Idle Shampoo", stock="6", cost="50")   # never sold, 300 on the shelf
    old = _product(tenant, wh, "OLD-1", "Old Mask", stock="4", cost="25")       # sold 40 days ago
    _none = _product(tenant, wh, "NONE-1", "Nothing Of It")                      # never sold, none in stock
    _sell(api, wh, cust, None, [(v, "1")])
    inv = _sell(api, wh, cust, None, [(old, "1")])
    with tenant_context(tenant, tenant.owner):
        SalesInvoice.objects.filter(pk=inv["id"]).update(date=timezone.localdate() - timezone.timedelta(days=40))
    data = api.get("/api/v1/reports/items_not_sold").data
    rows = {r["label"]: r for r in data["rows"]}
    assert v.name not in rows, "sold today"
    assert [r["label"] for r in data["rows"]][:2] == ["Idle Shampoo", "Old Mask"], "the most money first"
    assert (rows["Idle Shampoo"]["last_sold"], D(rows["Idle Shampoo"]["on_hand"]), D(rows["Idle Shampoo"]["stock_value"])) == ("Never", 6, D("300"))
    assert rows["Old Mask"]["days_since"] == 40 and D(rows["Old Mask"]["on_hand"]) == 3
    assert "Nothing Of It" in rows
    assert "Nothing Of It" not in {r["label"] for r in api.get("/api/v1/reports/items_not_sold", {"show": "In stock only"}).data["rows"]}
    assert "Old Mask" in {r["label"] for r in api.get("/api/v1/reports/items_not_sold", {"days": "30"}).data["rows"]}
    assert "Old Mask" not in {r["label"] for r in api.get("/api/v1/reports/items_not_sold", {"days": "45"}).data["rows"]}, "sold within 45 days"
    spec = {r["key"]: r for r in api.get("/api/v1/reports").data}["items_not_sold"]
    assert {"name": "days", "type": "number", "default": 30, "label": "Not Sold In Last (Days)"} in spec["params"]
    for fmt in ("csv", "xlsx", "pdf"):
        assert api.get("/api/v1/reports/items_not_sold", {"format": fmt}).status_code == 200


def test_the_biggest_seller_comes_first(api, tenant):
    """27 Nov 2026: by name, a sale person whose one sale all came back led the report — every first row Returns = Sales."""
    from apps.partners.models import SupplierRep
    from apps.sales.models import SalesInvoice
    from apps.sales.services import CreditNoteService

    wh, sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        aliza = SupplierRep.objects.create(supplier=sup, code="R-1", name="Aliza Saleem", phone="03001110000")
        naveed = SupplierRep.objects.create(supplier=sup, code="R-2", name="NAVEED", phone="03001110001")
    back = _sell(api, wh, cust, aliza, [(v, "1")])
    _sell(api, wh, cust, naveed, [(v, "3")])
    with tenant_context(tenant, tenant.owner):
        inv = SalesInvoice.objects.get(pk=back["id"])
        order_id = str(inv.order_id)
    assert api.post(f"/api/v1/sales-orders/{order_id}/mark-delivered", {}, format="json").status_code == 200
    with tenant_context(tenant, tenant.owner):
        inv = SalesInvoice.objects.get(pk=back["id"])
        line = inv.lines.first()
        CreditNoteService.post(CreditNoteService.create(customer=cust, invoice=inv, reason="all back", lines=[
            {"variant": v, "qty": "1", "unit_price": str(line.unit_price), "unit_cost": str(line.unit_cost), "condition": "good", "restock": True}]))
    rows = api.get("/api/v1/reports/product_by_rep").data["rows"]
    assert [r["sale_person"] for r in rows] == ["NAVEED", "Aliza Saleem"], rows
    assert D(rows[1]["sales"]) == D(rows[1]["returns"]) and D(rows[1]["net_sales"]) == 0, "all of hers came back"


def test_stock_valuation_at_dp_tp_and_rp(api, tenant):
    """Owner, 27 Nov 2026: "inventory valuation. give me in DP, TP and RP value"."""
    wh, _sup, v, _cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        v.cost_price, v.sale_price, v.retail_price = D("100"), D("150"), D("200")
        v.save(update_fields=["cost_price", "sale_price", "retail_price"])
        from apps.inventory.models import StockLevel

        on_hand = sum((lv.qty_on_hand for lv in StockLevel.objects.filter(variant=v)), D(0))
    data = api.get("/api/v1/reports/stock_valuation").data
    assert data["columns"] == ["warehouse", "label", "brand", "qty", "dp", "dp_value", "tp", "tp_value", "rp", "rp_value"]
    row = next(r for r in data["rows"] if r["label"] == v.name)
    assert (D(row["qty"]), D(row["dp_value"]), D(row["tp_value"]), D(row["rp_value"])) == (on_hand, on_hand * 100, on_hand * 150, on_hand * 200)
    assert data["column_labels"]["dp_value"] == "D.P Value" and D(data["totals"]["rp_value"]) >= on_hand * 200
    for fmt in ("csv", "xlsx", "pdf"):
        assert api.get("/api/v1/reports/stock_valuation", {"format": fmt}).status_code == 200
