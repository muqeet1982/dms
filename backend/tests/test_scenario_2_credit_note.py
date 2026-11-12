"""
The owner's Scenario 2, as restated on 13 Nov 2026: the order is confirmed, the warehouse issues it, the delivery note is
made, the invoice is raised, and no trip yet — then the customer cancels. The delivery note is cancelled (its stock goes
back to the shelf, free to sell), Accounts raises a credit note on the invoice, and with it posted the order is Cancelled.
A delivery note on a trip cannot be cancelled.
"""
from decimal import Decimal as D

import pytest

from apps.core.tenancy import tenant_context

from .test_returns_at_delivery import books, on_hand, order_of, out, w  # noqa: F401  (w is a fixture)

pytestmark = pytest.mark.django_db


def _available(w):
    from apps.inventory.services import StockService

    with tenant_context(w.tenant):
        return StockService.available(w.v, w.wh)


def _credit(api, w, inv, qty="5", expect=201):
    r = api.post("/api/v1/credit-notes", {"customer": str(w.cust.id), "invoice": inv["id"], "warehouse": str(w.wh.id), "reason": "customer cancelled", "post": True,
                                          "lines": [{"variant": str(w.v.id), "qty": qty, "restock": True, "condition": "good"}]}, format="json")
    assert r.status_code == expect, r.content
    return r


def test_scenario_2_delivery_note_cancelled_then_the_credit_note_cancels_the_order(api, w):
    from apps.inventory.models import StockMovement
    from apps.partners.services import PartnerLedgerService

    shelf, free, ledger = on_hand(w), _available(w), books(w)
    o, inv, _t, dn = out(api, w, trip=False)                              # 1-6: confirmed, reserved, issued, note, invoice; 7: no trip
    assert dn["status"] == "loaded" and inv["status"] == "posted" and on_hand(w) == shelf - 5

    # the issue the owner hit: the credit note asks for the delivery note first
    r = _credit(api, w, inv, expect=422)
    assert r.data["type"] == "not_delivered", r.content

    # 1-4 · Cancel delivery note: the issue is reversed and the stock is free again
    assert api.get(f"/api/v1/delivery-notes/{dn['id']}").data["can_cancel"] is True
    r = api.post(f"/api/v1/delivery-notes/{dn['id']}/cancel", {"reason": "customer cancelled"}, format="json")
    assert r.status_code == 200 and r.data["status"] == "cancelled" and r.data["can_cancel"] is False, r.content
    assert (on_hand(w), _available(w)) == (shelf, free), "Warehouse Available Stock: Restored"
    with tenant_context(w.tenant):
        assert StockMovement.objects.filter(reference_id=dn["id"], movement_type="issue_cancelled").count() == 1, "Stock Issue: Reversed"
    assert api.get(f"/api/v1/invoices/{inv['id']}").data["status"] == "posted", "the invoice waits for its credit note"
    assert order_of(api, o)["status"] == "confirmed"
    again = api.post(f"/api/v1/sales-orders/{o['id']}/load-form", {"send": True}, format="json")
    assert again.status_code == 422 and again.data["type"] == "awaiting_credit_note", "not issued again while the invoice still bills it"

    # 5 · the credit note on the original invoice; it moves no stock — the goods are already back
    cn = _credit(api, w, inv).data
    assert cn["status"] == "posted"
    assert (on_hand(w), _available(w)) == (shelf, free), "the stock is not counted twice"
    with tenant_context(w.tenant):
        assert not StockMovement.objects.filter(reference_id=cn["id"]).exists()

    # 6 · the order is Cancelled
    order = order_of(api, o)
    assert order["status"] == "cancelled", "Sale Order: Cancelled"
    assert api.get(f"/api/v1/delivery-notes/{dn['id']}").data["status"] == "cancelled", "Delivery Note: Cancelled"
    after = api.get(f"/api/v1/invoices/{inv['id']}").data
    assert D(after["balance"]) == 0 and D(after["credited_amount"]) == D(inv["total"]), "Invoice: Adjusted through Credit Note"
    assert not api.get("/api/v1/trips", {"search": o["number"]}).data.get("results", []), "Trip: Not Created"
    with tenant_context(w.tenant):
        assert PartnerLedgerService.balance("customer", w.cust.id) == 0
    moved = {k: v - ledger.get(k, D("0")) for k, v in books(w).items() if v != ledger.get(k, D("0"))}
    # Stock in Hand, COGS and Trade Receivables all back to where they were; the sale stands against Sales Returns
    assert set(moved) == {"Sales – Products", "Sales Returns"} and sum(moved.values()) == 0, moved


def test_a_delivery_note_on_a_trip_cannot_be_cancelled(api, w):
    _o, _inv, t, dn = out(api, w)
    r = api.post(f"/api/v1/delivery-notes/{dn['id']}/cancel", {}, format="json")
    assert r.status_code == 422 and r.data["type"] == "on_trip", r.content
    assert api.get(f"/api/v1/delivery-notes/{dn['id']}").data["can_cancel"] is False


def test_a_delivered_note_cannot_be_cancelled(api, w):
    _o, _inv, _t, dn = out(api, w, trip=False)
    assert api.post(f"/api/v1/delivery-notes/{dn['id']}/deliver", {"receiver_name": "Shop"}, format="json").status_code == 200
    r = api.post(f"/api/v1/delivery-notes/{dn['id']}/cancel", {}, format="json")
    assert r.status_code == 422 and r.data["type"] == "not_pending", r.content


def test_with_no_invoice_the_order_is_back_at_confirmed_and_holds_its_stock(api, w):
    shelf = on_hand(w)
    o, _inv, _t, dn = out(api, w, invoice=None, trip=False)
    assert api.post(f"/api/v1/delivery-notes/{dn['id']}/cancel", {}, format="json").status_code == 200
    assert on_hand(w) == shelf and order_of(api, o)["status"] == "confirmed"
    assert _available(w) == shelf - 5, "confirmed again, it holds its 5"
    again = api.post(f"/api/v1/sales-orders/{o['id']}/load-form", {"send": True}, format="json")
    assert again.status_code == 201, again.content


def test_a_draft_invoice_goes_with_the_note(api, w):
    o, inv, _t, dn = out(api, w, invoice="draft", trip=False)
    assert inv["status"] == "draft"
    assert api.post(f"/api/v1/delivery-notes/{dn['id']}/cancel", {}, format="json").status_code == 200
    assert api.get(f"/api/v1/invoices/{inv['id']}").data["status"] == "cancelled"
    assert order_of(api, o)["status"] == "confirmed"


def test_a_part_credit_leaves_the_order_and_deleting_the_credit_brings_it_back(api, w):
    shelf = on_hand(w)
    o, inv, _t, dn = out(api, w, trip=False)
    assert api.post(f"/api/v1/delivery-notes/{dn['id']}/cancel", {}, format="json").status_code == 200
    part = _credit(api, w, inv, qty="2").data
    assert order_of(api, o)["status"] == "confirmed", "3 still billed"
    rest = _credit(api, w, inv, qty="3").data
    assert order_of(api, o)["status"] == "cancelled" and on_hand(w) == shelf

    assert api.delete(f"/api/v1/credit-notes/{rest['id']}").status_code == 204
    assert order_of(api, o)["status"] == "confirmed", "the credit note gone, the order waits for it again"
    assert on_hand(w) == shelf, "the goods stay where they are: that credit note moved none"
    assert part["status"] == "posted"


def test_cancelling_a_note_is_its_own_switch(api, w):
    from unittest import mock

    from apps.accounts.models import TenantMembership
    from apps.accounts.page_permissions import PAGES

    _o, _inv, _t, dn = out(api, w, trip=False)
    not_owner = mock.patch.object(TenantMembership, "is_owner", new_callable=mock.PropertyMock, return_value=False)
    with not_owner, mock.patch.object(TenantMembership, "has_perm", lambda self, c: c != "delivery.cancel_delivery_note"):
        assert api.post(f"/api/v1/delivery-notes/{dn['id']}/cancel", {}, format="json").status_code == 403
    page = next(p for p in PAGES if p["key"] == "delivery_notes")
    assert "delivery.cancel_delivery_note" in [e["perm"] for e in page["extras"]]
