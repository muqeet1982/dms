"""
Deleting a posted credit note (owner, 12 Nov 2026): "give in role and permission option to on off delete credit note and
owner should have that option on. when delete, all entries related to it should be delete. in stock, in accounting etc."
A switch of its own on the Credit notes page; the owner always has it. Deleted, the return leaves nothing behind: the
invoice owes what it owed, the stock and every account read as before it, the manufacturer's claim is back, and neither a
journal nor a stock movement of it is left.
"""
from decimal import Decimal as D

import pytest

from apps.core.tenancy import tenant_context

from .test_returns_at_delivery import books, on_hand, order_of, out, stop, w  # noqa: F401  (w is a fixture)

pytestmark = pytest.mark.django_db


def _delivered(api, w, qty="5"):
    """Issued, invoiced and delivered at the shop: returns (order, invoice)."""
    o, inv, _t, dn = out(api, w, qty=qty)
    assert api.post(f"/api/v1/delivery-notes/{dn['id']}/deliver", {"receiver_name": "Shop"}, format="json").status_code == 200
    return o, inv


def _return(api, w, inv, qty="2", **extra):
    r = api.post("/api/v1/credit-notes", {"customer": str(w.cust.id), "invoice": inv["id"], "warehouse": str(w.wh.id), "reason": "returned", "post": True,
                                          "lines": [{"variant": str(w.v.id), "qty": qty, "restock": True, "condition": "good"}], **extra}, format="json")
    assert r.status_code == 201 and r.data["status"] == "posted", r.content
    return r.data


def _trail(w, cn_id):
    from apps.accounting.models import JournalEntry
    from apps.inventory.models import StockMovement
    from apps.sales.models import CreditNote

    with tenant_context(w.tenant):
        return {"note": CreditNote.objects.filter(pk=cn_id).exists(),
                "journals": JournalEntry.objects.filter(source_type="CreditNote", source_id=str(cn_id)).count(),
                "moves": StockMovement.objects.filter(reference_type="CreditNote", reference_id=str(cn_id)).count()}


def test_deleting_a_return_takes_every_trace_of_it_away(api, w):
    from apps.core.models import AuditLog
    from apps.sales.models import DiscountContribution

    with tenant_context(w.tenant, w.tenant.owner):
        DiscountContribution.objects.get_or_create(total_pct=D("10"), defaults={"distributor_pct": D("6"), "company_pct": D("4")})
        w.cust.default_discount_pct = D("10")
        w.cust.save(update_fields=["default_discount_pct"])
    o, inv = _delivered(api, w)
    before = {"books": books(w), "shelf": on_hand(w), "inv": api.get(f"/api/v1/invoices/{inv['id']}").data,
              "claim": D(api.get("/api/v1/company-claims", {"invoice": inv["id"]}).data["results"][0]["total"])}
    cn = _return(api, w, inv)
    assert on_hand(w) == before["shelf"] + 2 and D(api.get(f"/api/v1/invoices/{inv['id']}").data["balance"]) < D(before["inv"]["balance"])
    assert D(order_of(api, o)["lines"][0]["qty_returned"]) == 2
    assert _trail(w, cn["id"])["journals"] >= 1 and _trail(w, cn["id"])["moves"] == 1

    r = api.delete(f"/api/v1/credit-notes/{cn['id']}?reason=typed+on+the+wrong+invoice")
    assert r.status_code == 204, r.content
    assert _trail(w, cn["id"]) == {"note": False, "journals": 0, "moves": 0}, "nothing of it is left"
    assert on_hand(w) == before["shelf"], "stock: the 2 left the shelf again"
    assert books(w) == before["books"], "accounting: every account as before the return"
    after = api.get(f"/api/v1/invoices/{inv['id']}").data
    assert (after["status"], after["balance"], after["paid_amount"]) == (before["inv"]["status"], before["inv"]["balance"], before["inv"]["paid_amount"])
    assert D(api.get("/api/v1/company-claims", {"invoice": inv["id"]}).data["results"][0]["total"]) == before["claim"], "Keune's claim is back"
    assert D(order_of(api, o)["lines"][0]["qty_returned"]) == 0
    with tenant_context(w.tenant):
        log = AuditLog.objects.filter(action="credit_note.deleted", object_id=cn["id"]).first()
        assert log is not None and log.before["number"] == cn["number"] and log.before["reason"] == "typed on the wrong invoice", "the audit log remembers it"


def test_credit_used_on_another_invoice_comes_off_it_too(api, w):
    from apps.sales.models import CreditNote
    from apps.sales.services import CreditNoteService

    _o1, inv1 = _delivered(api, w)
    _o2, inv2 = _delivered(api, w)
    cn = _return(api, w, inv1, applied_to_invoice=False)               # kept as credit…
    with tenant_context(w.tenant, w.tenant.owner):
        CreditNoteService.apply(CreditNote.objects.get(pk=cn["id"]), [{"invoice": inv2["id"], "amount": "100"}])   # …and used on another invoice
    assert D(api.get(f"/api/v1/invoices/{inv2['id']}").data["paid_amount"]) == 100
    assert api.delete(f"/api/v1/credit-notes/{cn['id']}").status_code == 204
    i2 = api.get(f"/api/v1/invoices/{inv2['id']}").data
    assert D(i2["paid_amount"]) == 0 and D(i2["balance"]) == D(inv2["total"])


def test_a_stop_return_deleted_means_the_customer_kept_them(api, w):
    o, inv, t, dn = out(api, w)
    shelf, before = on_hand(w), books(w)
    r = stop(api, dn)                                                  # 1 of 5 back at the door
    assert r.status_code == 200 and r.data["credit_note"], r.content
    cn_id = r.data["credit_note"]["id"]
    assert api.delete(f"/api/v1/credit-notes/{cn_id}").status_code == 204
    assert _trail(w, cn_id) == {"note": False, "journals": 0, "moves": 0}
    note = api.get(f"/api/v1/delivery-notes/{dn['id']}").data
    assert note["status"] == "delivered" and D(note["lines"][0]["qty_delivered"]) == 5, "the stop reads Delivered"
    assert on_hand(w) == shelf and order_of(api, o)["status"] == "delivered"
    assert api.get(f"/api/v1/invoices/{inv['id']}").data["balance"] == inv["balance"]
    assert books(w) == before


def test_what_stops_a_delete(api, w):
    from apps.accounting.models import AccountingPeriod
    from apps.inventory.services import StockService
    from apps.sales.models import CreditNote

    _o, inv = _delivered(api, w)
    # refunded: that money went out
    cn = _return(api, w, inv, qty="1", applied_to_invoice=False, settlement={"choice": "refund", "refund": {"method": "cash"}})
    with tenant_context(w.tenant):
        assert CreditNote.objects.get(pk=cn["id"]).refunded_amount > 0
    r = api.delete(f"/api/v1/credit-notes/{cn['id']}")
    assert r.status_code == 422 and r.data["type"] == "refunded", r.content

    # the returned goods have been sold since: they cannot leave the shelf twice
    cn2 = _return(api, w, inv, qty="2")
    with tenant_context(w.tenant, w.tenant.owner):
        StockService.move(warehouse=w.wh, variant=w.v, qty=-on_hand(w), movement_type="adjustment")
    r = api.delete(f"/api/v1/credit-notes/{cn2['id']}")
    assert r.status_code == 422 and r.data["type"] == "insufficient_stock", r.content
    assert _trail(w, cn2["id"])["note"], "nothing was touched"

    # a closed month
    with tenant_context(w.tenant, w.tenant.owner):
        StockService.move(warehouse=w.wh, variant=w.v, qty=D("10"), movement_type="adjustment", unit_cost=D("100"))
        p = CreditNote.objects.get(pk=cn2["id"]).journal.period
        AccountingPeriod.objects.filter(pk=p.pk).update(status="closed")
    r = api.delete(f"/api/v1/credit-notes/{cn2['id']}")
    assert r.status_code == 422 and r.data["type"] == "period_closed", r.content


def test_the_switch_is_the_owners_unless_granted(api, w):
    from unittest import mock

    from apps.accounts.models import TenantMembership

    _o, inv = _delivered(api, w)
    cn = _return(api, w, inv, qty="1")
    not_owner = mock.patch.object(TenantMembership, "is_owner", new_callable=mock.PropertyMock, return_value=False)
    with not_owner, mock.patch.object(TenantMembership, "has_perm", lambda self, c: c != "sales.delete_posted_credit_note"):
        assert api.delete(f"/api/v1/credit-notes/{cn['id']}").status_code == 403, "the page's Delete alone is for drafts"
    with not_owner, mock.patch.object(TenantMembership, "has_perm", lambda self, c: True):
        assert api.delete(f"/api/v1/credit-notes/{cn['id']}").status_code == 204, "granted, the role may"


def test_the_switch_is_on_the_roles_page():
    from apps.accounts.page_permissions import PAGES, pages_as_json

    cn = next(p for p in PAGES if p["key"] == "credit_notes")
    assert [e["perm"] for e in cn["extras"]] == ["sales.delete_posted_credit_note"], cn["extras"]
    assert "sales.delete_posted_credit_note" in str(pages_as_json()), "the role editor shows it"
