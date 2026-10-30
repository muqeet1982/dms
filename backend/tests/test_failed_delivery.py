"""
A stop that did not reach the customer (owner, 30 Oct 2026: "what if out for delivery and not received by customer?"):

- the customer does not want the goods → Customer rejected: the sale reversed, the goods back once, no return
- shop closed, nobody to receive them → Deliver again: the SAME delivery note and the SAME invoice go out again, paid
  or not — never a second delivery note or a second invoice beside the failed one (TDN-SO-00044 had two invoices)
"""
from decimal import Decimal

import pytest

from apps.core.tenancy import tenant_context

from .test_returns import _on_hand, _trip_invoice, shop  # noqa: F401  (shop is a fixture)

pytestmark = pytest.mark.django_db
D = Decimal


def _fail(api, inv, reason="Shop closed"):
    dn = api.get(f"/api/v1/invoices/{inv['id']}").data["delivery_note"]
    r = api.post(f"/api/v1/delivery-notes/{dn}/deliver", {"failed": True, "failure_reason": reason}, format="json")
    assert r.status_code == 200, r.content
    return str(dn)


def _invoices(tenant, order_id):
    from apps.sales.models import SalesInvoice

    with tenant_context(tenant):
        return list(SalesInvoice.objects.filter(order_id=order_id).values_list("number", "status"))


def _live_journals(tenant, inv_id):
    from apps.accounting.models import JournalEntry

    with tenant_context(tenant):
        return JournalEntry.objects.filter(source_id=inv_id, status="posted").count()


def _new_trip(api, tenant, wh, o):
    from apps.delivery.models import Driver, Vehicle

    with tenant_context(tenant, tenant.owner):
        driver = Driver.objects.create(name="Bilal", phone="03007654321")
        vehicle = Vehicle.objects.create(reg_no=f"LEC-{driver.pk.hex[-6:]}")
    return api.post("/api/v1/trips", {"warehouse": str(wh.id), "driver": str(driver.id), "vehicle": str(vehicle.id),
                                      "delivery_rep": str(tenant.owner.id), "order_ids": [o["id"]]}, format="json")


def test_shop_closed_the_same_goods_and_invoice_go_out_again_on_the_next_trip(api, tenant, shop):
    from apps.core.models import Notification

    wh, _s, v, cust = shop
    before = _on_hand(tenant, wh, v)
    o, inv, trip = _trip_invoice(api, tenant, wh, cust, v, qty="3")
    dn = _fail(api, inv)
    with tenant_context(tenant):
        note = Notification.objects.filter(type="invoice.customer_refused").latest("created_at")
    assert note.data["order_id"] == o["id"] and "Deliver again" in note.body, "the office is sent to the order to decide"
    assert api.post(f"/api/v1/trips/{trip['id']}/close", {"override": True}, format="json").status_code == 200
    assert _on_hand(tenant, wh, v) == before, "the closed trip brought the goods home"
    assert api.get(f"/api/v1/sales-orders/{o['id']}").data["status"] == "confirmed"

    # a second delivery beside the failed one would bill the order twice
    for path, body in ((f"/api/v1/sales-orders/{o['id']}/mark-delivered", {"receiver_name": "Shop"}),
                       (f"/api/v1/sales-orders/{o['id']}/load-form", {"send": True})):
        r = api.post(path, body, format="json")
        assert r.status_code == 422 and r.data["type"] == "failed_delivery", (path, r.content)
    t = _new_trip(api, tenant, wh, o)
    assert t.status_code == 422 and t.data["type"] == "failed_delivery", t.content

    again = api.post(f"/api/v1/delivery-notes/{dn}/deliver-again", {"reason": "open tomorrow"}, format="json")
    assert again.status_code == 200, again.content
    assert again.data["status"] == "loaded" and again.data["trip"] is None
    assert _on_hand(tenant, wh, v) == before - 3, "the goods left the shelf again"
    assert api.get(f"/api/v1/sales-orders/{o['id']}").data["status"] == "issued"

    t2 = _new_trip(api, tenant, wh, o)
    assert t2.status_code == 201, t2.content
    notes = api.get(f"/api/v1/delivery-notes?order={o['id']}").data
    notes = notes["results"] if isinstance(notes, dict) else notes
    assert [str(n["id"]) for n in notes] == [dn] and str(notes[0]["trip"]) == t2.data["id"], "the same note rides the next trip"
    assert api.post(f"/api/v1/trips/{t2.data['id']}/start", {}, format="json").status_code == 200
    assert _on_hand(tenant, wh, v) == before - 3, "starting the trip takes nothing out twice"
    d = api.post(f"/api/v1/delivery-notes/{dn}/deliver", {"receiver_name": "Shop"}, format="json")
    assert d.status_code == 200 and d.data["status"] == "delivered", d.content
    assert api.post(f"/api/v1/trips/{t2.data['id']}/close", {"override": True}, format="json").status_code == 200
    assert _on_hand(tenant, wh, v) == before - 3
    assert _invoices(tenant, o["id"]) == [(inv["number"], "posted")], "one order, one invoice"
    assert _live_journals(tenant, inv["id"]) == 2, "booked once"


def test_deliver_again_before_the_trip_closes_keeps_the_goods_on_the_van(api, tenant, shop):
    wh, _s, v, cust = shop
    before = _on_hand(tenant, wh, v)
    o, inv, trip = _trip_invoice(api, tenant, wh, cust, v, qty="3")
    dn = _fail(api, inv)
    again = api.post(f"/api/v1/delivery-notes/{dn}/deliver-again", {}, format="json")
    assert again.status_code == 200, again.content
    assert again.data["status"] == "dispatched" and str(again.data["trip"]) == trip["id"]
    assert all(D(l["qty_returned"]) == 0 for l in again.data["lines"]), "the trip's close must not restock them"
    assert _on_hand(tenant, wh, v) == before - 3, "nothing moved: still on the van"
    assert api.post(f"/api/v1/delivery-notes/{dn}/deliver", {"receiver_name": "Shop"}, format="json").status_code == 200
    assert api.post(f"/api/v1/trips/{trip['id']}/close", {"override": True}, format="json").status_code == 200
    assert _on_hand(tenant, wh, v) == before - 3
    assert _invoices(tenant, o["id"]) == [(inv["number"], "posted")]


def test_a_paid_invoice_goes_out_again_as_it_is(api, tenant, shop):
    """TDN records the cash before the van's stops are marked: the receipt stays, the invoice stays Paid."""
    wh, _s, v, cust = shop
    o, inv, trip = _trip_invoice(api, tenant, wh, cust, v, qty="2")
    pay = api.post("/api/v1/accounting/payments", {"direction": "in", "partner_type": "customer", "partner_id": str(cust.id), "amount": inv["total"], "method": "cash",
                                                   "allocations": [{"document_type": "sales_invoice", "document_id": inv["id"], "amount": inv["total"]}]}, format="json")
    assert pay.status_code == 201, pay.content
    dn = _fail(api, inv)
    assert api.post(f"/api/v1/trips/{trip['id']}/close", {"override": True}, format="json").status_code == 200
    assert api.post(f"/api/v1/delivery-notes/{dn}/deliver-again", {}, format="json").status_code == 200
    assert api.post(f"/api/v1/delivery-notes/{dn}/deliver", {"receiver_name": "Shop"}, format="json").status_code == 200
    i = api.get(f"/api/v1/invoices/{inv['id']}").data
    assert i["status"] == "paid" and D(i["cash_paid"]) == D(inv["total"])
    assert _invoices(tenant, o["id"]) == [(inv["number"], "paid")]


def test_refused_at_the_door_the_order_reverses_the_sale_after_the_trip_closed(api, tenant, shop):
    wh, _s, v, cust = shop
    before = _on_hand(tenant, wh, v)
    o, inv, trip = _trip_invoice(api, tenant, wh, cust, v, qty="3")
    dn = _fail(api, inv, "Customer refused the delivery")
    assert api.post(f"/api/v1/trips/{trip['id']}/close", {"override": True}, format="json").status_code == 200
    r = api.post(f"/api/v1/sales-orders/{o['id']}/customer-rejected", {"reason": "did not order it"}, format="json")
    assert r.status_code == 200, r.content
    assert r.data["status"] == "rejected"
    assert _invoices(tenant, o["id"]) == [(inv["number"], "cancelled")] and _live_journals(tenant, inv["id"]) == 0
    assert _on_hand(tenant, wh, v) == before, "home once — with the trip, not again by the rejection"
    late = api.post(f"/api/v1/delivery-notes/{dn}/deliver-again", {}, format="json")
    assert late.status_code == 422


def test_rejected_while_the_van_is_still_out_the_trips_close_does_not_restock_again(api, tenant, shop):
    wh, _s, v, cust = shop
    before = _on_hand(tenant, wh, v)
    o, inv, trip = _trip_invoice(api, tenant, wh, cust, v, qty="3")
    _fail(api, inv, "Customer refused the delivery")
    assert api.post(f"/api/v1/invoices/{inv['id']}/reject", {"reason": "refused"}, format="json").status_code == 200
    assert _on_hand(tenant, wh, v) == before
    assert api.post(f"/api/v1/trips/{trip['id']}/close", {"override": True}, format="json").status_code == 200
    assert _on_hand(tenant, wh, v) == before, "counted once"
