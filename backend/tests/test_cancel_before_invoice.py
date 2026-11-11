"""
The owner's Scenario 1 (11 Nov 2026): a confirmed order holds its stock; cancelled before any invoice, it closes as
Cancelled, the hold is released and the stock is free again. No invoice, no stock issue, no credit note. The same holds
when the load form was already sent, or the warehouse already issued the goods: they come home first.
"""
from decimal import Decimal as D

import pytest

from apps.core.tenancy import tenant_context

from .conftest import issue_order, seed_basic

pytestmark = pytest.mark.django_db


def _confirmed(api, wh, v, cust, qty="5"):
    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True, "lines": [{"variant": str(v.id), "qty": qty}]}, format="json")
    assert r.status_code == 201 and r.data["status"] == "confirmed", r.content
    return r.data


def _stock(tenant, v, wh):
    from apps.inventory.models import StockLevel
    from apps.inventory.services import StockService

    with tenant_context(tenant):
        rows = StockLevel.objects.filter(variant=v, warehouse=wh)
        return {"on_hand": sum(r.qty_on_hand for r in rows), "reserved": sum(r.qty_reserved for r in rows), "available": StockService.available(v, wh)}


def _paperwork(tenant, order_id):
    from apps.delivery.models import DeliveryNote, PickList
    from apps.inventory.models import StockMovement
    from apps.sales.models import CreditNote, SalesInvoice, StockReservation

    with tenant_context(tenant):
        return {
            "invoices": SalesInvoice.objects.filter(order_id=order_id).exclude(status="cancelled").count(),
            "credit_notes": CreditNote.objects.filter(invoice__order_id=order_id).count(),
            "live_holds": StockReservation.objects.filter(order_line__order_id=order_id, released=False).count(),
            "live_forms": PickList.objects.filter(orders__id=order_id).exclude(status="cancelled").count(),
            "live_notes": DeliveryNote.objects.filter(order_id=order_id).exclude(status="cancelled").count(),
            "moves": StockMovement.objects.filter(reference_id=str(order_id)).count(),
        }


def test_scenario_1_cancel_a_confirmed_order_before_its_invoice(api, tenant):
    wh, _s, v, cust = seed_basic(tenant)                       # 100 pcs
    before = _stock(tenant, v, wh)
    o = _confirmed(api, wh, v, cust, "5")
    held = _stock(tenant, v, wh)
    assert held == {"on_hand": before["on_hand"], "reserved": D("5"), "available": before["available"] - 5}, "3 · reserved against the order"

    r = api.post(f"/api/v1/sales-orders/{o['id']}/cancel", {"reason": "customer asked"}, format="json")
    assert r.status_code == 200 and r.data["status"] == "cancelled", r.content
    assert _stock(tenant, v, wh) == before, "6 · released and available again"
    assert _paperwork(tenant, o["id"]) == {"invoices": 0, "credit_notes": 0, "live_holds": 0, "live_forms": 0, "live_notes": 0, "moves": 0}


def test_cancel_with_the_load_form_sent_takes_the_form_back(api, tenant):
    wh, _s, v, cust = seed_basic(tenant)
    before = _stock(tenant, v, wh)
    o = _confirmed(api, wh, v, cust, "5")
    assert api.post(f"/api/v1/sales-orders/{o['id']}/load-form", {"send": True}, format="json").status_code == 201
    assert api.post(f"/api/v1/sales-orders/{o['id']}/cancel", {"reason": "customer asked"}, format="json").status_code == 200
    assert _stock(tenant, v, wh) == before
    p = _paperwork(tenant, o["id"])
    assert p["live_forms"] == 0 and p["live_holds"] == 0 and p["invoices"] == 0 and p["credit_notes"] == 0, p


def test_cancel_after_the_warehouse_issued_brings_the_goods_home(api, tenant):
    wh, _s, v, cust = seed_basic(tenant)
    before = _stock(tenant, v, wh)
    o = _confirmed(api, wh, v, cust, "5")
    issue_order(api, o["id"])
    assert _stock(tenant, v, wh)["on_hand"] == before["on_hand"] - 5, "the 5 left the shelf"
    assert api.post(f"/api/v1/sales-orders/{o['id']}/cancel", {"reason": "customer asked"}, format="json").status_code == 200
    assert _stock(tenant, v, wh) == before, "back on the shelf and free"
    p = _paperwork(tenant, o["id"])
    assert p["live_forms"] == 0 and p["live_notes"] == 0 and p["invoices"] == 0 and p["credit_notes"] == 0, p
