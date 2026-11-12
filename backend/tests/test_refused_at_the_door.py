"""
Owner, 13 Nov 2026: "when delivery reached to customer then customer says, i do not want it and want to cancel partial or
complete order. i think partial working but complete cancel not working. make sure check both use case and update stock
and account". A part refusal already settled at the stop; a whole one sat there — 35 TDN stops of 9-12 Nov read Returned
with the goods off the shelf and the invoices still owed. Now both settle as the stop is saved: good units back on the
shelf, a posted invoice credited by a credit note (owner's choice), a draft cancelled, and the order of a whole refusal
closes as Rejected (Customer rejected). "Delivery failed" is two things: the customer refused (settled now) or could not
deliver (the goods stay on the note for Deliver again).
"""
from decimal import Decimal as D

import pytest

from apps.core.tenancy import tenant_context

from .test_returns_at_delivery import books, close, invoice_of, on_hand, order_of, out, stop, w  # noqa: F401  (w is a fixture)

pytestmark = pytest.mark.django_db


def _moved(before, after):
    return {k: v - before.get(k, D("0")) for k, v in after.items() if v != before.get(k, D("0"))}


def _refused_button(api, dn, **extra):
    return api.post(f"/api/v1/delivery-notes/{dn['id']}/deliver", {"failed": True, "refused": True, "failure_reason": "Customer refused the delivery", **extra}, format="json")


def _the_sale_is_undone(api, w, o, inv, shelf, ledger, r):
    note = r.data
    assert note["status"] == "returned" and note["credit_note"], note
    assert D(note["credit_note"]["total"]) == D(inv["total"]), "the whole invoice credited"
    assert on_hand(w) == shelf, "stock: every unit back on the shelf"
    after = invoice_of(api, inv)
    assert D(after["balance"]) == 0, "the customer owes nothing"
    moved = _moved(ledger, books(w))
    assert set(moved) == {"Sales – Products", "Sales Returns"} and sum(moved.values()) == 0, f"Stock in Hand, COGS and Receivables as before: {moved}"
    order = order_of(api, o)
    assert order["status"] == "rejected", "Customer rejected"
    assert order["rejection_reason"].startswith(f"Customer refused {note['number']}")


def test_whole_refusal_entered_as_every_unit_returned(api, w):
    shelf, ledger = on_hand(w), books(w)
    o, inv, t, dn = out(api, w)
    r = stop(api, dn, back="5")
    assert r.status_code == 200, r.content
    _the_sale_is_undone(api, w, o, inv, shelf, ledger, r)
    close(api, t)
    assert on_hand(w) == shelf, "the trip's close does not put them back a second time"
    moved = _moved(ledger, books(w))
    assert set(moved) == {"Sales – Products", "Sales Returns"} and sum(moved.values()) == 0, f"nor post them again: {moved}"


def test_the_refused_button_does_the_same(api, w):
    shelf, ledger = on_hand(w), books(w)
    o, inv, _t, dn = out(api, w)
    r = _refused_button(api, dn)
    assert r.status_code == 200, r.content
    assert r.data["failure_reason"] == "Customer refused the delivery"
    _the_sale_is_undone(api, w, o, inv, shelf, ledger, r)
    again = api.post(f"/api/v1/delivery-notes/{dn['id']}/deliver-again", {}, format="json")
    assert again.status_code == 422, "a refused order does not go out again"


def test_could_not_deliver_waits_and_customer_rejected_later_settles_it_the_same_way(api, w):
    shelf, ledger = on_hand(w), books(w)
    o, inv, _t, dn = out(api, w)
    r = api.post(f"/api/v1/delivery-notes/{dn['id']}/deliver", {"failed": True, "failure_reason": "Shop closed"}, format="json")
    assert r.status_code == 200 and r.data["status"] == "failed" and not r.data["credit_note"], r.content
    assert on_hand(w) == shelf - 5 and order_of(api, o)["status"] == "dispatched", "still out: it may go again"
    assert D(invoice_of(api, inv)["balance"]) == D(inv["total"])

    r = api.post(f"/api/v1/sales-orders/{o['id']}/customer-rejected", {"reason": "does not want it any more"}, format="json")
    assert r.status_code == 200 and r.data["status"] == "rejected", r.content
    note = api.get(f"/api/v1/delivery-notes/{dn['id']}").data
    assert note["status"] == "returned" and note["credit_note"] and D(note["credit_note"]["total"]) == D(inv["total"]), "a credit note, not a quiet cancel"
    assert invoice_of(api, inv)["status"] != "cancelled" and D(invoice_of(api, inv)["balance"]) == 0
    assert on_hand(w) == shelf
    moved = _moved(ledger, books(w))
    assert set(moved) == {"Sales – Products", "Sales Returns"} and sum(moved.values()) == 0, moved


def test_whole_refusal_with_a_draft_invoice_cancels_the_draft(api, w):
    shelf, ledger = on_hand(w), books(w)
    o, inv, _t, dn = out(api, w, invoice="draft")
    r = stop(api, dn, back="5")
    assert r.status_code == 200 and not r.data["credit_note"], r.content
    assert invoice_of(api, inv)["status"] == "cancelled" and on_hand(w) == shelf
    assert order_of(api, o)["status"] == "rejected" and books(w) == ledger, "nothing was ever on the books"


def test_whole_refusal_with_no_invoice(api, w):
    shelf, ledger = on_hand(w), books(w)
    o, _inv, _t, dn = out(api, w, invoice=None)
    assert _refused_button(api, dn).status_code == 200
    assert on_hand(w) == shelf and order_of(api, o)["status"] == "rejected" and books(w) == ledger


def test_whole_refusal_damaged_is_expensed_not_restocked(api, w):
    shelf = on_hand(w)
    o, inv, _t, dn = out(api, w)
    r = stop(api, dn, back="5", reason="damaged", condition="damaged")
    assert r.status_code == 200 and r.data["credit_note"], r.content
    assert on_hand(w) == shelf - 5, "broken goods are not stock"
    assert D(invoice_of(api, inv)["balance"]) == 0 and order_of(api, o)["status"] == "rejected"
    assert any("Damaged" in k for k in books(w)), books(w)


def test_part_refusal_still_settles_the_part(api, w):
    shelf, ledger = on_hand(w), books(w)
    o, inv, _t, dn = out(api, w)
    r = stop(api, dn, back="2")
    assert r.status_code == 200 and r.data["status"] == "partially_delivered" and r.data["credit_note"], r.content
    assert on_hand(w) == shelf - 3, "the 2 refused are back, the 3 kept are sold"
    assert D(r.data["credit_note"]["total"]) == (D(inv["total"]) * 2 / 5).quantize(D("0.01"))
    assert D(invoice_of(api, inv)["balance"]) == D(inv["total"]) - D(r.data["credit_note"]["total"])
    assert order_of(api, o)["status"] == "delivered", "the refused units are finished, not a balance to send"
    moved = _moved(ledger, books(w))
    assert moved["COGS"] == D(300) and moved["Stock in Hand"] == D(-300), "only the 3 kept are a cost of sale"


def test_another_delivery_on_the_way_keeps_the_order_open(api, w):
    """An order is closed by a refusal only when nothing else of it is still going to the customer."""
    from apps.delivery.models import DeliveryNote
    from apps.sales.services import OrderService

    o, _inv, _t, dn = out(api, w)
    with tenant_context(w.tenant):
        note = DeliveryNote.objects.get(pk=dn["id"])
        other = DeliveryNote.objects.create(number="X-DN-2", order=note.order, customer=note.customer, warehouse=note.warehouse, status="loaded")
        assert OrderService.rejected_at_door(note.order, note) is None
        other.delete()
