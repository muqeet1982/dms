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


def test_scenario_2_cancel_after_the_invoice_before_dispatch(api, tenant):
    """Owner's Scenario 2: here an invoice is raised on what the warehouse issued, so the goods are issued first.
    Cancelled before any trip: the invoice is cancelled and its entries come off the books, the goods come home, the
    order closes as Cancelled. No credit note, no trip."""
    from apps.accounting.models import JournalLine
    from apps.sales.models import CreditNote, SalesInvoice

    wh, _s, v, cust = seed_basic(tenant)
    before = _stock(tenant, v, wh)
    o = _confirmed(api, wh, v, cust, "5")
    issue_order(api, o["id"])
    inv = api.post(f"/api/v1/sales-orders/{o['id']}/invoice", {"post": True}, format="json").data[0]
    assert inv["status"] == "posted"

    r = api.post(f"/api/v1/sales-orders/{o['id']}/cancel", {"reason": "customer asked"}, format="json")
    assert r.status_code == 200 and r.data["status"] == "cancelled", r.content
    assert _stock(tenant, v, wh) == before, "back on the shelf and free"
    with tenant_context(tenant):
        assert SalesInvoice.objects.get(pk=inv["id"]).status == "cancelled"
        assert CreditNote.objects.count() == 0
        live = JournalLine.objects.filter(entry__source_id=inv["id"], entry__status="posted")
        assert sum(l.debit - l.credit for l in live if l.account.code.startswith("1-")) in (0, D("0")) or not live.exists(), "its entries are off the books"
    p = _paperwork(tenant, o["id"])
    assert p["live_forms"] == 0 and p["live_notes"] == 0 and p["invoices"] == 0, p


def test_scenario_2_with_money_received_asks_for_a_return(api, tenant):
    wh, _s, v, cust = seed_basic(tenant)
    o = _confirmed(api, wh, v, cust, "5")
    issue_order(api, o["id"])
    inv = api.post(f"/api/v1/sales-orders/{o['id']}/invoice", {"post": True}, format="json").data[0]
    from apps.accounting.services import PaymentService

    with tenant_context(tenant, tenant.owner):
        PaymentService.record(direction="in", partner_type="customer", partner_id=str(cust.id), partner_name=cust.name, amount=D("100"), method="cash",
                              allocations=[{"document_type": "sales_invoice", "document_id": inv["id"], "amount": D("100")}])
    r = api.post(f"/api/v1/sales-orders/{o['id']}/cancel", {"reason": "customer asked"}, format="json")
    assert r.status_code == 422 and r.data["type"] == "invoice_settled", r.content


def test_scenario_3_cancel_on_the_road_the_trip_carries_on(api, tenant):
    """Owner's Scenario 3: invoiced, on a started trip with another customer's order, not yet delivered. Cancelled: its
    note and invoice are cancelled, its goods come back into the warehouse, the order closes, and the trip stays on the
    road for the other stop, completing when that one is delivered."""
    from apps.delivery.models import DeliveryNote, Driver
    from apps.partners.models import Customer
    from apps.sales.models import CreditNote, SalesInvoice

    tenant.settings = {**(tenant.settings or {}), "trip_needs_posted_invoice": True, "delivery_needs_trip": True}
    tenant.save(update_fields=["settings"])
    wh, _s, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        other = Customer.objects.create(code="C-2", name="Second Shop", credit_limit=D("100000"))
        driver = Driver.objects.create(name="Akram", phone="03001234567")
    before = _stock(tenant, v, wh)
    a, b = _confirmed(api, wh, v, cust, "5"), _confirmed(api, wh, v, other, "3")
    invs = {}
    for o in (a, b):
        issue_order(api, o["id"])
        invs[o["id"]] = api.post(f"/api/v1/sales-orders/{o['id']}/invoice", {"post": True}, format="json").data[0]["id"]
    trip = api.post("/api/v1/trips", {"delivery_rep": str(tenant.owner.id), "warehouse": str(wh.pk), "driver": str(driver.pk), "order_ids": [a["id"], b["id"]]}, format="json")
    assert trip.status_code == 201, trip.content
    assert api.post(f"/api/v1/trips/{trip.data['id']}/start", {}, format="json").status_code == 200
    assert _stock(tenant, v, wh)["on_hand"] == before["on_hand"] - 8, "8 pcs on the vehicle"

    r = api.post(f"/api/v1/sales-orders/{a['id']}/cancel", {"reason": "customer cancelled while the van was out"}, format="json")
    assert r.status_code == 200 and r.data["status"] == "cancelled", r.content
    with tenant_context(tenant):
        assert DeliveryNote.objects.get(order_id=a["id"]).status == "cancelled"
        assert SalesInvoice.objects.get(pk=invs[a["id"]]).status == "cancelled"
        assert CreditNote.objects.count() == 0
    assert _stock(tenant, v, wh)["on_hand"] == before["on_hand"] - 3, "the cancelled 5 are back in the warehouse"
    assert _stock(tenant, v, wh)["available"] == before["available"] - 3
    t = api.get(f"/api/v1/trips/{trip.data['id']}").data
    assert t["status"] == "out_for_delivery", "the trip carries on"

    with tenant_context(tenant):
        nb = DeliveryNote.objects.get(order_id=b["id"])
    assert api.post(f"/api/v1/delivery-notes/{nb.pk}/deliver", {"receiver_name": "Shop"}, format="json").status_code == 200
    assert api.get(f"/api/v1/trips/{trip.data['id']}").data["status"] == "completed", "the other stop ends it"
