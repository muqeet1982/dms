"""Product default location (warehouse bin) and opening quantity."""
from decimal import Decimal

import pytest

from apps.core.tenancy import tenant_context

pytestmark = pytest.mark.django_db


def setup_wh(tenant, code="MAIN"):
    from apps.inventory.models import Bin, Warehouse

    with tenant_context(tenant, tenant.owner):
        wh = Warehouse.objects.create(code=code, name="Main", type="main")
        return wh, Bin.objects.create(warehouse=wh, code="A-01", name="Rack A shelf 1")


def new_product(api, **extra):
    from apps.catalog.models import UnitOfMeasure

    with tenant_context(extra.pop("tenant"), None):
        uom = UnitOfMeasure.objects.get(code="PC")
    body = {"sku": "TL-1", "name": "Shelf Product", "base_uom": str(uom.id), "cost_price": "100", "sale_price": "120", **extra}
    r = api.post("/api/v1/products", body, format="json")
    assert r.status_code == 201, r.content
    return r.data


def test_product_location_and_opening_qty(api, tenant):
    from apps.accounting.models import JournalEntry
    from apps.inventory.models import StockLevel, StockMovement

    wh, bin_ = setup_wh(tenant)
    p = new_product(api, tenant=tenant, location=str(bin_.id), opening_qty="48")
    assert str(p["location"]) == str(bin_.id) and p["location_name"] == "MAIN / A-01 — Rack A shelf 1"
    with tenant_context(tenant, tenant.owner):
        mv = StockMovement.objects.get(movement_type="opening")
        assert mv.qty == Decimal("48") and mv.unit_cost == Decimal("100") and mv.bin_id == bin_.id and mv.warehouse_id == wh.id
        assert StockLevel.objects.get(variant__sku="TL-1").qty_on_hand == Decimal("48")
        je = JournalEntry.objects.order_by("-created_at").first()  # Dr inventory / Cr opening equity
        assert sum(line.debit for line in je.lines.all()) == Decimal("4800")
    detail = api.get(f"/api/v1/products/{p['id']}?with_stock=1").data
    assert Decimal(detail["stock_on_hand"]) == Decimal("48") and detail["location_name"].endswith("Rack A shelf 1")


def test_product_without_opening_qty_has_no_movement(api, tenant):
    from apps.inventory.models import StockMovement

    _, bin_ = setup_wh(tenant)
    new_product(api, tenant=tenant, location=str(bin_.id))
    with tenant_context(tenant, tenant.owner):
        assert not StockMovement.objects.exists()


def test_opening_qty_uses_given_warehouse_and_cost(api, tenant):
    from apps.inventory.models import StockMovement, Warehouse

    setup_wh(tenant)
    with tenant_context(tenant, tenant.owner):
        other = Warehouse.objects.create(code="SUB", name="Sub store", type="sub")
    new_product(api, tenant=tenant, opening_qty="10", opening_warehouse=str(other.id), opening_unit_cost="90")
    with tenant_context(tenant, tenant.owner):
        mv = StockMovement.objects.get(movement_type="opening")
        assert mv.warehouse_id == other.id and mv.unit_cost == Decimal("90") and mv.bin_id is None


def test_locations_crud_and_delete_rules(api, tenant):
    from apps.catalog.models import Product

    wh, bin_ = setup_wh(tenant)
    rows = api.get("/api/v1/settings/bins").data
    assert [(b["code"], b["label"], b["products_count"]) for b in rows] == [("A-01", "MAIN / A-01 — Rack A shelf 1", 0)]

    r = api.post("/api/v1/settings/bins", {"warehouse": str(wh.id), "code": "B-02", "name": "Rack B"}, format="json")
    assert r.status_code == 201 and r.data["label"] == "MAIN / B-02 — Rack B"
    assert api.patch(f"/api/v1/settings/bins/{r.data['id']}", {"name": "Rack B shelf 2"}, format="json").data["name"] == "Rack B shelf 2"

    p = new_product(api, tenant=tenant, location=str(bin_.id), opening_qty="5")
    assert api.get("/api/v1/settings/bins").data[0]["products_count"] == 1
    assert api.delete(f"/api/v1/settings/bins/{bin_.id}").status_code == 422  # holds stock

    assert api.delete(f"/api/v1/settings/bins/{r.data['id']}").status_code == 204  # empty location deletes
    with tenant_context(tenant, tenant.owner):
        assert Product.objects.get(pk=p["id"]).location_id == bin_.id  # untouched by the other deletion


def test_stock_put_away_in_a_rack_is_still_sellable(api, tenant):
    """
    Receiving puts goods in a rack, so every outward move that does not name one has to find them: a sale, a
    transfer or a purchase return must draw from the racks, not report "on hand 0".
    """
    from apps.inventory.models import Bin, StockMovement
    from apps.inventory.services import StockService

    wh, bin_ = setup_wh(tenant)
    p = new_product(api, tenant=tenant, location=str(bin_.id), opening_qty="48")
    with tenant_context(tenant, tenant.owner):
        from apps.catalog.models import ProductVariant

        variant = ProductVariant.objects.get(sku="TL-1")
        second = Bin.objects.create(warehouse=wh, code="B-01", name="Rack B shelf 1")
        StockService.move(warehouse=wh, variant=variant, qty=10, movement_type="opening", unit_cost="100", bin=second)

        mv = StockService.move(warehouse=wh, variant=variant, qty=-20, movement_type="sale_dispatch")
        assert mv.bin_id == bin_.id, "taken out of the rack that holds it"
        assert StockService.on_hand(variant, wh)[0] == Decimal("38")

        # more than any single rack holds: the draw is split and the ledger says which racks it left
        StockService.move(warehouse=wh, variant=variant, qty=-30, movement_type="sale_dispatch")
        assert StockService.on_hand(variant, wh)[0] == Decimal("8")
        out = StockMovement.objects.filter(variant=variant, qty__lt=0).order_by("created_at")
        assert sorted({str(m.bin.code) for m in out}) == ["A-01", "B-01"]

        # and what is not there is still refused, counting every rack
        with pytest.raises(Exception) as bad:
            StockService.move(warehouse=wh, variant=variant, qty=-9, movement_type="sale_dispatch")
        assert "on hand 8.000" in str(bad.value), str(bad.value)
    assert Decimal(api.get(f"/api/v1/products/{p['id']}?with_stock=1").data["stock_on_hand"]) == Decimal("8")
