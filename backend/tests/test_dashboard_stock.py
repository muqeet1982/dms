"""
The dashboard's stock panel (owner, 11 Nov 2026): what we hold at cost (ready to sell, booked for confirmed orders, out
for delivery) and, apart from it, what customers received this month.
"""
from decimal import Decimal as D

import pytest

from apps.core.tenancy import tenant_context

from .conftest import issue_order, seed_basic

pytestmark = pytest.mark.django_db


def _order(api, wh, v, cust, qty):
    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True, "lines": [{"variant": str(v.id), "qty": qty}]}, format="json")
    assert r.status_code == 201 and r.data["status"] == "confirmed", r.content
    return r.data


def test_the_stock_panel_shows_where_the_stock_is(api, tenant):
    from apps.delivery.models import DeliveryNote

    wh, _sup, v, cust = seed_basic(tenant)          # 100 pcs at cost 100
    tenant.settings = {**(tenant.settings or {}), "reserve_stock_on_confirm": True}
    tenant.save(update_fields=["settings"])
    _order(api, wh, v, cust, "5")                    # booked, still in the warehouse
    issue_order(api, _order(api, wh, v, cust, "3")["id"])                   # out for delivery
    delivered = _order(api, wh, v, cust, "2")
    issue_order(api, delivered["id"])
    with tenant_context(tenant):
        note = DeliveryNote.objects.get(order_id=delivered["id"])
    assert api.post(f"/api/v1/delivery-notes/{note.pk}/deliver", {"receiver_name": "Shop"}, format="json").status_code == 200

    s = api.get("/api/v1/dashboard").data["stock"]
    units = {k: D(s[k]["units"]) for k in ("held", "free", "booked", "out", "delivered_month")}
    assert units == {"held": 98, "free": 90, "booked": 5, "out": 3, "delivered_month": 2}, units
    assert D(s["held"]["value"]) == D("9800") and D(s["free"]["value"]) + D(s["booked"]["value"]) + D(s["out"]["value"]) == D(s["held"]["value"])
    assert D(s["delivered_month"]["value"]) == D("200")


def test_a_big_count_prints_as_a_plain_number(api, tenant):
    from apps.inventory.services import StockService

    wh, _sup, v, _cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        StockService.move(warehouse=wh, variant=v, qty=D("1100"), movement_type="adjustment", unit_cost=D("100"))
    assert api.get("/api/v1/dashboard").data["stock"]["held"]["units"] == "1200", "not 1.2E+3"
