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
    assert data["columns"][:3] == ["sale_person", "label", "qty"] and data["column_labels"]["label"] == "Product Name"
    got = {(r["sale_person"], r["label"]): D(r["qty"]) for r in data["rows"]}
    assert got == {("Anza", shade.name): 1, ("NAVEED", v.name): 2, ("NAVEED", shade.name): 3}
    assert data["totals"]["net_sales"] == api.get("/api/v1/reports/sales_by_rep").data["totals"]["net_sales"], "adds up to Sales by Sale Person"
    one = api.get("/api/v1/reports/product_by_rep", {"rep": str(anza.id)}).data
    assert [(r["sale_person"], r["label"]) for r in one["rows"]] == [("Anza", shade.name)]
    spec = {r["key"]: r for r in api.get("/api/v1/reports").data}["product_by_rep"]
    assert spec["opens_on"] == "month" and spec["pick_columns"]
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
