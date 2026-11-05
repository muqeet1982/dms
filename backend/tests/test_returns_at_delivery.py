"""
Goods that come back during delivery (owner, 5 Nov 2026: "how to handle when return during delivery? is shoud also update
customer ledger and update account and chart of account. also report if item going back to stock or wasteage").

Saving the stop does it all at once, because a trip's close cannot be relied on: TDN had closed 3 of its 37 trips, and
TDN-SO-00149 sat Partially delivered until someone pressed Mark Delivered and billed the returned unit again.

Throughout: 5 units at 150, cost 100, 100 on the shelf, 1 comes back.
"""
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from apps.core.tenancy import tenant_context

from .conftest import issue_order, seed_basic

pytestmark = pytest.mark.django_db
SALES = "Sales – Products"


@pytest.fixture
def w(tenant):
    from apps.delivery.models import Driver, Vehicle

    wh, _s, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        driver, vehicle = Driver.objects.create(name="Akram", phone="03001234567"), Vehicle.objects.create(reg_no="LEB-1")
    return SimpleNamespace(wh=wh, v=v, cust=cust, driver=driver, vehicle=vehicle, tenant=tenant)


def on_hand(w):
    from apps.inventory.models import StockLevel

    with tenant_context(w.tenant):
        return sum((s.qty_on_hand for s in StockLevel.objects.filter(warehouse=w.wh, variant=w.v)), D("0"))


def books(w):
    """Every account with a balance, by name: debit positive, credit negative."""
    from django.db.models import Sum

    from apps.accounting.models import GL_LIVE, JournalLine

    with tenant_context(w.tenant):
        rows = JournalLine.objects.filter(entry__status__in=GL_LIVE).values("account__name").annotate(d=Sum("debit"), c=Sum("credit"))
        return {r["account__name"]: (r["d"] or D(0)) - (r["c"] or D(0)) for r in rows if (r["d"] or D(0)) != (r["c"] or D(0))}


def out(api, w, invoice="post", trip=True, qty="5"):
    """An order issued, billed or not, and out on a started trip. Returns (order, invoice, trip, note)."""
    o = api.post("/api/v1/sales-orders", {"customer": str(w.cust.id), "warehouse": str(w.wh.id), "confirm": True, "lines": [{"variant": str(w.v.id), "qty": qty}]}, format="json").data
    issue_order(api, o["id"])
    inv = None
    if invoice:
        r = api.post(f"/api/v1/sales-orders/{o['id']}/invoice", {"post": invoice == "post"}, format="json")
        assert r.status_code in (200, 201), r.content
        inv = r.data[0]
    t = None
    if trip:
        t = api.post("/api/v1/trips", {"warehouse": str(w.wh.id), "driver": str(w.driver.id), "vehicle": str(w.vehicle.id), "delivery_rep": str(w.tenant.owner.id), "order_ids": [o["id"]]}, format="json").data
        assert api.post(f"/api/v1/trips/{t['id']}/start", {}, format="json").status_code == 200
    notes = api.get("/api/v1/delivery-notes", {"order": o["id"]}).data
    return o, inv, t, (notes["results"] if isinstance(notes, dict) else notes)[0]


def stop(api, dn, back="1", reason="rejected", condition="good", **extra):
    note = api.get(f"/api/v1/delivery-notes/{dn['id']}").data
    lines = [{"line_id": l["id"], "qty_delivered": str(D(l["qty_loaded"]) - D(back)), "qty_returned": back, "return_reason": reason, "return_condition": condition} for l in note["lines"]]
    return api.post(f"/api/v1/delivery-notes/{dn['id']}/deliver", {"delivered": lines, "receiver_name": "Shop", **extra}, format="json")


def close(api, t):
    r = api.post(f"/api/v1/trips/{t['id']}/close", {"override": True}, format="json")
    assert r.status_code == 200, r.content


def order_of(api, o):
    return api.get(f"/api/v1/sales-orders/{o['id']}").data


def invoice_of(api, inv):
    return api.get(f"/api/v1/invoices/{inv['id']}").data


def ledger_closes_at(w):
    from apps.partners.services import PartnerLedgerService

    with tenant_context(w.tenant):
        return D(PartnerLedgerService.ledger("customer", w.cust.pk)["closing_balance"])


# ---------------------------------------------------------------- part of the stop comes back

def test_a_good_unit_back_settles_everything_when_the_stop_is_saved(api, w):
    from apps.sales.models import CreditNote

    o, inv, t, dn = out(api, w)
    assert on_hand(w) == 95 and ledger_closes_at(w) == 750
    r = stop(api, dn)
    assert r.status_code == 200, r.content
    assert r.data["status"] == "partially_delivered", "the stop itself delivered part"
    assert D(r.data["credit_note"]["total"]) == 150 and r.data["can_undo_return"] is True
    assert r.data["lines"][0]["return_result"] == "Back To Stock"

    assert on_hand(w) == 96, "back on the shelf, with no trip close"
    assert ledger_closes_at(w) == 600, "the customer's ledger already reads it"
    assert books(w) == {"Trade Receivables": D(600), "Stock in Hand": D(-400), SALES: D(-750), "Sales Returns": D(150), "COGS": D(400)}
    i = invoice_of(api, inv)
    assert D(i["balance"]) == 600 and D(i["credited_amount"]) == 150 and D(i["cash_paid"]) == 0 and i["status"] == "posted"
    with tenant_context(w.tenant):
        cn = CreditNote.objects.get(invoice_id=inv["id"])
        assert str(cn.delivery_note_id) == dn["id"] and cn.status == "posted" and cn.date is not None
        line = cn.lines.get()
        assert (line.condition, line.restock, line.stock_back, line.reason) == ("good", True, True, "Rejected by customer")

    od = order_of(api, o)
    assert od["status"] == "delivered", "what came back is finished: nothing is left to send of it"
    assert D(od["lines"][0]["qty_delivered"]) == 4 and D(od["lines"][0]["qty_returned"]) == 1


def test_the_trips_close_counts_nothing_a_second_time(api, w):
    o, inv, t, dn = out(api, w)
    stop(api, dn)
    before = books(w)
    close(api, t)
    assert on_hand(w) == 96 and books(w) == before
    assert order_of(api, o)["status"] == "delivered"


def test_a_returned_unit_is_not_offered_again(api, w):
    """What led to TDN-DN-00149-2: the order stuck, Mark Delivered pressed, the same unit billed again."""
    o, inv, t, dn = out(api, w)
    stop(api, dn)
    offered = api.get("/api/v1/sales-orders", {"status__in": "confirmed,partially_delivered", "no_load_form": 1, "warehouse": str(w.wh.id)}).data["results"]
    assert o["id"] not in [x["id"] for x in offered], "New Load Form leaves it out"
    assert api.post(f"/api/v1/sales-orders/{o['id']}/mark-delivered", {"receiver_name": "x"}, format="json").status_code == 422
    assert api.post(f"/api/v1/sales-orders/{o['id']}/load-form", {"send": True}, format="json").status_code in (400, 422)
    assert on_hand(w) == 96


def test_a_real_balance_still_gets_its_own_load_form(api, w):
    """Ordered 5, 3 issued, 1 of those back: the 2 never sent are a balance, the 1 that came back is not."""
    from apps.delivery.models import PickList

    o = api.post("/api/v1/sales-orders", {"customer": str(w.cust.id), "warehouse": str(w.wh.id), "confirm": True, "lines": [{"variant": str(w.v.id), "qty": "5"}]}, format="json").data
    pl = api.post(f"/api/v1/sales-orders/{o['id']}/load-form", {"send": True}, format="json").data
    assert api.post(f"/api/v1/pick-lists/{pl['id']}/issue", {"issues": [{"line_id": l["id"], "qty_issued": "3"} for l in pl["lines"]]}, format="json").status_code == 200
    notes = api.get("/api/v1/delivery-notes", {"order": o["id"]}).data
    dn = (notes["results"] if isinstance(notes, dict) else notes)[0]
    assert stop(api, dn).status_code == 200
    assert order_of(api, o)["status"] == "partially_delivered"
    assert on_hand(w) == 98
    with tenant_context(w.tenant):
        assert D(next(r for r in _rows("pending_orders")["rows"] if r["number"] == o["number"])["qty_pending"]) == 2
    offered = api.get("/api/v1/sales-orders", {"status__in": "confirmed,partially_delivered", "no_load_form": 1, "warehouse": str(w.wh.id)}).data["results"]
    assert o["id"] in [x["id"] for x in offered]
    second = api.post(f"/api/v1/sales-orders/{o['id']}/load-form", {"send": True}, format="json")
    assert second.status_code == 201, second.content
    assert sum(D(l["qty_to_pick"]) for l in second.data["lines"]) == 2, "the balance, not the returned unit"
    with tenant_context(w.tenant):
        assert PickList.objects.filter(orders=o["id"]).count() == 2


def test_a_damaged_unit_is_expensed_once_and_stays_off_the_shelf(api, w):
    """The replay of 5 Nov: the close's adjustment and the credit note each expensed it, 100 of cost showing as 200."""
    from apps.inventory.models import StockAdjustment
    from apps.sales.models import CreditNote

    o, inv, t, dn = out(api, w)
    r = stop(api, dn, reason="rejected", condition="damaged")
    assert r.status_code == 200, r.content
    assert r.data["lines"][0]["return_result"] == "Wastage"
    close(api, t)
    assert on_hand(w) == 95, "not back on the shelf"
    assert books(w) == {"Trade Receivables": D(600), "Stock in Hand": D(-500), SALES: D(-750), "Sales Returns": D(150), "COGS": D(400), "Damaged Goods": D(100)}, "the 5 that left, once"
    with tenant_context(w.tenant):
        assert not StockAdjustment.objects.exists(), "the credit note expensed it; no second write-off"
        line = CreditNote.objects.get(invoice_id=inv["id"]).lines.get()
        assert (line.condition, line.restock, line.stock_back) == ("damaged", False, False)
    assert order_of(api, o)["status"] == "delivered"


@pytest.mark.parametrize("reason", ["damaged", "shortage"])
def test_a_broken_or_missing_unit_never_goes_back_on_the_shelf_whatever_was_sent(api, w, reason):
    _o, _inv, _t, dn = out(api, w)
    r = stop(api, dn, reason=reason, condition="good")     # the phone sends good for anything but "damaged"
    assert r.status_code == 200, r.content
    assert r.data["lines"][0]["return_condition"] == "damaged" and on_hand(w) == 95


def test_no_invoice_yet_the_draft_bills_what_was_kept_and_no_credit_note_is_needed(api, w):
    from apps.sales.models import CreditNote

    o, _inv, t, dn = out(api, w, invoice=None)
    r = stop(api, dn)
    assert r.status_code == 200, r.content
    assert r.data["credit_note"] is None and on_hand(w) == 96
    assert order_of(api, o)["status"] == "delivered"
    with tenant_context(w.tenant):
        assert not CreditNote.objects.exists()
    draft = api.get(f"/api/v1/invoices/{r.data['invoice_id']}").data
    assert draft["status"] == "draft" and D(draft["total"]) == 600
    assert api.post(f"/api/v1/invoices/{draft['id']}/post", {}, format="json").status_code == 200
    b = books(w)
    assert b == {"Trade Receivables": D(600), "Stock in Hand": D(-400), SALES: D(-600), "COGS": D(400)}
    close(api, t)
    assert on_hand(w) == 96


def test_no_invoice_yet_a_damaged_unit_is_written_off_once(api, w):
    from apps.inventory.models import StockAdjustment

    _o, _inv, t, dn = out(api, w, invoice=None)
    r = stop(api, dn, reason="rejected", condition="damaged")
    assert r.status_code == 200, r.content
    assert on_hand(w) == 95
    assert books(w) == {"Stock in Hand": D(-100), "Damaged Goods": D(100)}
    assert api.post(f"/api/v1/invoices/{r.data['invoice_id']}/post", {}, format="json").status_code == 200
    close(api, t)
    assert books(w) == {"Trade Receivables": D(600), "Stock in Hand": D(-500), SALES: D(-600), "COGS": D(400), "Damaged Goods": D(100)}
    with tenant_context(w.tenant):
        assert StockAdjustment.objects.count() == 1


def test_a_draft_made_before_delivery_comes_down_to_what_was_kept(api, w):
    from apps.sales.models import CreditNote

    with tenant_context(w.tenant, w.tenant.owner):
        w.cust.default_discount_pct = D("10")
        w.cust.save(update_fields=["default_discount_pct"])
    _o, inv, t, dn = out(api, w, invoice="draft")
    assert D(inv["total"]) == 675
    r = stop(api, dn)
    assert r.status_code == 200, r.content
    i = invoice_of(api, inv)
    assert i["status"] == "draft" and D(i["total"]) == 540 and D(i["lines"][0]["qty"]) == 4, "4 at 150, less the same 10%"
    with tenant_context(w.tenant):
        assert not CreditNote.objects.exists()
    assert api.post(f"/api/v1/invoices/{inv['id']}/post", {}, format="json").status_code == 200
    assert ledger_closes_at(w) == 540 and on_hand(w) == 96
    close(api, t)
    assert on_hand(w) == 96


def test_cash_taken_at_the_stop_settles_what_is_left(api, w):
    _o, inv, _t, dn = out(api, w)
    r = stop(api, dn, cash_collected="600")
    assert r.status_code == 200, r.content
    i = invoice_of(api, inv)
    assert i["status"] == "paid" and D(i["cash_paid"]) == 600 and D(i["credited_amount"]) == 150 and D(i["balance"]) == 0
    assert ledger_closes_at(w) == 0


def test_already_paid_the_credit_is_the_customers_to_keep_or_refund(api, w):
    _o, inv, _t, dn = out(api, w)
    api.post("/api/v1/accounting/payments", {"direction": "in", "partner_type": "customer", "partner_id": str(w.cust.id), "amount": "750", "method": "cash",
                                             "allocations": [{"document_type": "sales_invoice", "document_id": inv["id"], "amount": "750"}]}, format="json")
    r = stop(api, dn)
    assert r.status_code == 200, r.content
    assert invoice_of(api, inv)["status"] == "paid"
    assert D(api.get(f"/api/v1/credit-notes/{r.data['credit_note']['id']}").data["available_credit"]) == 150
    assert D(api.get(f"/api/v1/customers/{w.cust.id}").data["available_credit"]) == 150
    assert ledger_closes_at(w) == -150


def test_the_manufacturers_share_and_the_preview_match_the_note(api, w):
    from apps.sales.models import DiscountContribution

    with tenant_context(w.tenant, w.tenant.owner):
        DiscountContribution.objects.get_or_create(total_pct=D("10"), defaults={"distributor_pct": D("6"), "company_pct": D("4")})
        w.cust.default_discount_pct = D("10")
        w.cust.advance_income_tax_rate = D("0.5")
        w.cust.save(update_fields=["default_discount_pct", "advance_income_tax_rate"])
    _o, inv, _t, dn = out(api, w)
    note = api.get(f"/api/v1/delivery-notes/{dn['id']}").data
    p = api.post(f"/api/v1/delivery-notes/{dn['id']}/return-preview", {"delivered": [{"line_id": note["lines"][0]["id"], "qty_returned": "2"}]}, format="json")
    assert p.status_code == 200, p.content
    r = stop(api, dn, back="2")
    assert r.status_code == 200, r.content
    cn = api.get(f"/api/v1/credit-notes/{r.data['credit_note']['id']}").data
    assert D(p.data["credit"]) == D(cn["total"]) and D(cn["advance_tax_total"]) > 0, "the screen says what the note credits, to the paisa"
    i = invoice_of(api, inv)
    assert D(p.data["balance_after"]) == D(i["balance"])
    assert D(cn["discount"]) == 30, "10% on the two that came back"
    claim = api.get("/api/v1/company-claims", {"invoice": inv["id"]}).data["results"][0]
    assert D(claim["total"]) == D("18"), "Keune funds the 3 kept: 4% of 450"


def test_the_preview_is_nothing_for_a_draft_or_a_stop_refused_outright(api, w):
    _o, _inv, _t, dn = out(api, w, invoice="draft")
    note = api.get(f"/api/v1/delivery-notes/{dn['id']}").data
    line = note["lines"][0]["id"]
    assert D(api.post(f"/api/v1/delivery-notes/{dn['id']}/return-preview", {"delivered": [{"line_id": line, "qty_returned": "1"}]}, format="json").data["credit"]) == 0
    _o2, _inv2, _t2, dn2 = out(api, w)
    note2 = api.get(f"/api/v1/delivery-notes/{dn2['id']}").data
    p = api.post(f"/api/v1/delivery-notes/{dn2['id']}/return-preview", {"delivered": [{"line_id": note2["lines"][0]["id"], "qty_returned": "5"}]}, format="json")
    assert D(p.data["credit"]) == 0, "nothing kept: that is Customer rejected or Deliver again, not a credit note"


def test_good_and_damaged_of_one_item_are_each_treated_as_what_they_are(api, w):
    """Two lines of one item: the old automatic split forced the first it met to good, damaged or not."""
    from apps.delivery.models import DeliveryNoteLine

    _o, inv, _t, dn = out(api, w)
    with tenant_context(w.tenant, w.tenant.owner):
        l1 = DeliveryNoteLine.objects.get(delivery_note_id=dn["id"])
        l2 = DeliveryNoteLine.objects.create(delivery_note_id=dn["id"], order_line=l1.order_line, variant=l1.variant, qty_loaded=D("2"), unit_cost=l1.unit_cost)
        l1.qty_loaded = D("3")
        l1.save(update_fields=["qty_loaded"])
    body = {"receiver_name": "Shop", "delivered": [
        {"line_id": str(l1.id), "qty_delivered": "2", "qty_returned": "1", "return_reason": "damaged", "return_condition": "damaged"},
        {"line_id": str(l2.id), "qty_delivered": "1", "qty_returned": "1", "return_reason": "rejected", "return_condition": "good"}]}
    r = api.post(f"/api/v1/delivery-notes/{dn['id']}/deliver", body, format="json")
    assert r.status_code == 200, r.content
    cn = api.get(f"/api/v1/credit-notes/{r.data['credit_note']['id']}").data
    assert sorted((l["condition"], l["stock_back"], D(l["qty"])) for l in cn["lines"]) == [("damaged", False, D(1)), ("good", True, D(1))]
    assert on_hand(w) == 96
    b = books(w)
    assert b["Damaged Goods"] == 100 and b["COGS"] == 300 and b["Trade Receivables"] == 450


def test_every_unit_must_be_accounted_for(api, w):
    _o, _inv, _t, dn = out(api, w)
    note = api.get(f"/api/v1/delivery-notes/{dn['id']}").data
    r = api.post(f"/api/v1/delivery-notes/{dn['id']}/deliver", {"delivered": [{"line_id": note["lines"][0]["id"], "qty_delivered": "3", "qty_returned": "1"}]}, format="json")
    assert r.status_code == 422 and r.data["type"] == "qty_mismatch", r.content


def test_a_stop_is_saved_once(api, w):
    _o, _inv, _t, dn = out(api, w)
    assert stop(api, dn).status_code == 200
    again = stop(api, dn)
    assert again.status_code == 422, "a second save would credit the customer twice"
    assert on_hand(w) == 96 and ledger_closes_at(w) == 600


def test_damaged_goods_reach_damaged_goods_even_when_transit_damage_points_elsewhere(api, w):
    """TDN renamed Transit Damage's account Packing Material: a return's write-off must not land there."""
    from apps.accounting.models import Account

    with tenant_context(w.tenant, w.tenant.owner):
        Account.objects.filter(code="5-01-007-0001").update(name="Packing Material")
    _o, _inv, t, dn = out(api, w, invoice=None)
    stop(api, dn, reason="damaged", condition="damaged")
    close(api, t)
    b = books(w)
    assert b["Damaged Goods"] == 100 and "Packing Material" not in b


def test_with_no_trip_it_works_the_same(api, w):
    o, inv, _t, dn = out(api, w, trip=False)
    r = stop(api, dn)
    assert r.status_code == 200, r.content
    assert on_hand(w) == 96 and ledger_closes_at(w) == 600 and order_of(api, o)["status"] == "delivered"
    assert D(invoice_of(api, inv)["credited_amount"]) == 150


def test_a_later_return_of_goods_the_customer_kept_is_a_return_of_its_own(api, w):
    _o, inv, t, dn = out(api, w)
    stop(api, dn)
    close(api, t)
    later = api.post("/api/v1/credit-notes", {"customer": str(w.cust.id), "invoice": inv["id"], "post": True, "reason": "came back next week",
                                              "lines": [{"variant": str(w.v.id), "qty": "1", "unit_price": "150", "restock": True, "condition": "good"}]}, format="json")
    assert later.status_code == 201, later.content
    assert later.data["lines"][0]["stock_back"] is False and on_hand(w) == 97, "this one is moved by the return"
    assert ledger_closes_at(w) == 450


# ---------------------------------------------------------------- undo

def test_undo_puts_everything_back_as_if_delivered(api, w):
    from apps.sales.models import CreditNote, DiscountContribution

    with tenant_context(w.tenant, w.tenant.owner):
        DiscountContribution.objects.get_or_create(total_pct=D("10"), defaults={"distributor_pct": D("6"), "company_pct": D("4")})
        w.cust.default_discount_pct = D("10")
        w.cust.save(update_fields=["default_discount_pct"])
    o, inv, t, dn = out(api, w)
    before, on_shelf = books(w), on_hand(w)
    claim_before = D(api.get("/api/v1/company-claims", {"invoice": inv["id"]}).data["results"][0]["total"])
    r = stop(api, dn)
    assert D(api.get("/api/v1/company-claims", {"invoice": inv["id"]}).data["results"][0]["total"]) < claim_before
    u = api.post(f"/api/v1/delivery-notes/{dn['id']}/undo-return", {"reason": "typed by mistake"}, format="json")
    assert u.status_code == 200, u.content
    assert u.data["status"] == "delivered" and u.data["credit_note"] is None and u.data["can_undo_return"] is False
    assert D(u.data["lines"][0]["qty_delivered"]) == 5 and D(u.data["lines"][0]["qty_returned"]) == 0
    assert on_hand(w) == on_shelf
    assert books(w) == before, "every entry the return made is reversed"
    assert D(api.get("/api/v1/company-claims", {"invoice": inv["id"]}).data["results"][0]["total"]) == claim_before
    i = invoice_of(api, inv)
    assert D(i["balance"]) == D(inv["total"]) and D(i["credited_amount"]) == 0 and i["status"] == "posted"
    od = order_of(api, o)
    assert od["status"] == "delivered" and D(od["lines"][0]["qty_delivered"]) == 5 and D(od["lines"][0]["qty_returned"]) == 0
    with tenant_context(w.tenant):
        assert CreditNote.objects.get(pk=r.data["credit_note"]["id"]).status == "cancelled"
    close(api, t)
    assert on_hand(w) == on_shelf
    # and the unit can still be returned the ordinary way, as what it really is
    cn = api.post("/api/v1/credit-notes", {"customer": str(w.cust.id), "invoice": inv["id"], "post": True, "reason": "really damaged",
                                           "lines": [{"variant": str(w.v.id), "qty": "1", "unit_price": "150", "restock": False, "condition": "damaged"}]}, format="json")
    assert cn.status_code == 201, cn.content
    assert books(w)["Damaged Goods"] == 100


def test_undo_of_a_damaged_unit_no_invoice_billed_reverses_the_write_off_and_bills_it_again(api, w):
    from apps.inventory.models import StockAdjustment

    _o, _inv, _t, dn = out(api, w, invoice=None)
    before = books(w)
    r = stop(api, dn, reason="rejected", condition="damaged")
    assert D(api.get(f"/api/v1/invoices/{r.data['invoice_id']}").data["total"]) == 600
    assert api.post(f"/api/v1/delivery-notes/{dn['id']}/undo-return", {}, format="json").status_code == 200
    assert books(w) == before
    assert D(api.get(f"/api/v1/invoices/{r.data['invoice_id']}").data["total"]) == 750, "the draft bills all 5 again"
    with tenant_context(w.tenant):
        assert StockAdjustment.objects.get().status == "cancelled"


def _two_items(api, w, invoice):
    """An order of 2 of the item and 2 of a second one, issued, billed or not."""
    from apps.catalog.models import UnitOfMeasure
    from apps.catalog.services import ProductService
    from apps.inventory.services import StockService
    from apps.partners.models import Supplier

    with tenant_context(w.tenant, w.tenant.owner):
        p = ProductService.create_product(w.tenant, {"sku": "P-2", "name": "Colour 8.1", "supplier": Supplier.objects.first(), "base_uom": UnitOfMeasure.objects.get(code="PC"), "cost_price": D("100"), "sale_price": D("150")})
        other = p.variants.first()
        StockService.move(warehouse=w.wh, variant=other, qty=D("100"), movement_type="opening", unit_cost=D("100"))
    o = api.post("/api/v1/sales-orders", {"customer": str(w.cust.id), "warehouse": str(w.wh.id), "confirm": True, "lines": [{"variant": str(w.v.id), "qty": "2"}, {"variant": str(other.id), "qty": "2"}]}, format="json").data
    issue_order(api, o["id"])
    r = api.post(f"/api/v1/sales-orders/{o['id']}/invoice", {"post": invoice == "post"}, format="json")
    assert r.status_code in (200, 201), r.content
    notes = api.get("/api/v1/delivery-notes", {"order": o["id"]}).data
    return o, r.data[0], (notes["results"] if isinstance(notes, dict) else notes)[0], other


def test_undo_brings_back_a_draft_line_that_came_off_altogether(api, w):
    """One item all back, the other kept: the draft loses a line, and the undo has to bring it back."""
    o, inv, dn, other = _two_items(api, w, "draft")
    assert D(inv["total"]) == 600
    lines = {str(l["variant"]): l for l in api.get(f"/api/v1/delivery-notes/{dn['id']}").data["lines"]}
    body = {"receiver_name": "Shop", "delivered": [{"line_id": lines[str(w.v.id)]["id"], "qty_delivered": "0", "qty_returned": "2", "return_reason": "rejected"},
                                                   {"line_id": lines[str(other.id)]["id"], "qty_delivered": "2", "qty_returned": "0"}]}
    r = api.post(f"/api/v1/delivery-notes/{dn['id']}/deliver", body, format="json")
    assert r.status_code == 200 and r.data["status"] == "partially_delivered", r.content
    i = invoice_of(api, inv)
    assert [str(l["variant"]) for l in i["lines"]] == [str(other.id)] and D(i["total"]) == 300
    assert on_hand(w) == 100
    assert api.post(f"/api/v1/delivery-notes/{dn['id']}/undo-return", {}, format="json").status_code == 200
    i = invoice_of(api, inv)
    assert sorted(str(l["variant"]) for l in i["lines"]) == sorted([str(w.v.id), str(other.id)]) and D(i["total"]) == 600
    assert on_hand(w) == 98


def test_one_item_all_back_and_another_kept_on_a_posted_invoice(api, w):
    o, inv, dn, other = _two_items(api, w, "post")
    lines = {str(l["variant"]): l for l in api.get(f"/api/v1/delivery-notes/{dn['id']}").data["lines"]}
    body = {"receiver_name": "Shop", "delivered": [{"line_id": lines[str(w.v.id)]["id"], "qty_delivered": "0", "qty_returned": "2", "return_reason": "rejected"},
                                                   {"line_id": lines[str(other.id)]["id"], "qty_delivered": "2", "qty_returned": "0"}]}
    r = api.post(f"/api/v1/delivery-notes/{dn['id']}/deliver", body, format="json")
    assert r.status_code == 200, r.content
    assert D(r.data["credit_note"]["total"]) == 300 and D(invoice_of(api, inv)["balance"]) == 300
    assert on_hand(w) == 100 and order_of(api, o)["status"] == "delivered"


def test_undo_is_refused_once_the_credit_has_gone_somewhere_else(api, w):
    _o, inv, _t, dn = out(api, w)
    api.post("/api/v1/accounting/payments", {"direction": "in", "partner_type": "customer", "partner_id": str(w.cust.id), "amount": "750", "method": "cash",
                                             "allocations": [{"document_type": "sales_invoice", "document_id": inv["id"], "amount": "750"}]}, format="json")
    r = stop(api, dn)
    rf = api.post(f"/api/v1/credit-notes/{r.data['credit_note']['id']}/refund", {"amount": "150", "method": "cash"}, format="json")
    assert rf.status_code == 200, rf.content
    u = api.post(f"/api/v1/delivery-notes/{dn['id']}/undo-return", {}, format="json")
    assert u.status_code == 422 and u.data["type"] == "credit_used", u.content
    assert on_hand(w) == 96, "nothing moved"


def test_undo_is_refused_when_the_invoice_went_to_the_books_without_the_units(api, w):
    _o, _inv, _t, dn = out(api, w, invoice=None)
    r = stop(api, dn)
    assert api.post(f"/api/v1/invoices/{r.data['invoice_id']}/post", {}, format="json").status_code == 200
    u = api.post(f"/api/v1/delivery-notes/{dn['id']}/undo-return", {}, format="json")
    assert u.status_code == 422 and u.data["type"] == "invoice_posted", u.content


def test_there_is_nothing_to_undo_on_a_stop_with_no_return(api, w):
    _o, _inv, _t, dn = out(api, w)
    assert api.post(f"/api/v1/delivery-notes/{dn['id']}/deliver", {"receiver_name": "Shop"}, format="json").status_code == 200
    u = api.post(f"/api/v1/delivery-notes/{dn['id']}/undo-return", {}, format="json")
    assert u.status_code == 422 and u.data["type"] == "no_return"


def test_undoing_needs_the_right_to_complete_deliveries(api, w):
    from rest_framework.test import APIClient

    from apps.accounts.models import User

    _o, _inv, _t, dn = out(api, w)
    stop(api, dn)
    role = api.post("/api/v1/roles", {"name": "Looks only", "role_type": "internal", "permissions": ["delivery.view_delivery_note"]}, format="json").data
    api.post("/api/v1/users", {"full_name": "Viewer", "email": "viewer@probe.pk", "password": "Abcd-1234!", "roles": [role["id"]]}, format="json")
    User.objects.filter(email="viewer@probe.pk").update(must_change_password=False)
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {c.post('/api/v1/auth/login', {'identifier': 'viewer@probe.pk', 'password': 'Abcd-1234!'}, format='json').data['access']}", HTTP_X_TENANT_ID=str(w.tenant.id))
    assert c.post(f"/api/v1/delivery-notes/{dn['id']}/undo-return", {}, format="json").status_code == 403
    assert c.post(f"/api/v1/delivery-notes/{dn['id']}/return-preview", {"delivered": []}, format="json").status_code == 403


# ---------------------------------------------------------------- a stop refused outright: the 30 Oct rule stands

def test_refused_outright_stays_the_offices_decision(api, w):
    from apps.sales.models import CreditNote

    o, inv, t, dn = out(api, w)
    r = stop(api, dn, back="5")
    assert r.status_code == 200, r.content
    assert r.data["status"] == "returned" and r.data["credit_note"] is None and r.data["can_undo_return"] is False
    with tenant_context(w.tenant):
        assert not CreditNote.objects.exists()
    assert on_hand(w) == 95, "on the van until the office decides"
    assert api.post(f"/api/v1/sales-orders/{o['id']}/customer-rejected", {"reason": "did not order it"}, format="json").status_code == 200
    assert on_hand(w) == 100, "Customer rejected puts them back straight away, with no trip close"
    close(api, t)
    assert on_hand(w) == 100
    assert ledger_closes_at(w) == 0


def test_refused_and_damaged_customer_rejected_writes_them_off_instead_of_restocking(api, w):
    o, inv, t, dn = out(api, w)
    r = stop(api, dn, back="5", reason="rejected", condition="damaged")
    assert r.status_code == 200, r.content
    assert api.post(f"/api/v1/sales-orders/{o['id']}/customer-rejected", {"reason": "broken"}, format="json").status_code == 200
    assert on_hand(w) == 95, "broken goods do not go back as sellable"
    close(api, t)
    assert on_hand(w) == 95
    assert books(w) == {"Stock in Hand": D(-500), "Damaged Goods": D(500)}, "the sale is off the books, the loss on them once"


def test_damaged_goods_do_not_go_out_again_as_they_were(api, w):
    _o, _inv, _t, dn = out(api, w)
    stop(api, dn, back="5", reason="rejected", condition="damaged")
    r = api.post(f"/api/v1/delivery-notes/{dn['id']}/deliver-again", {}, format="json")
    assert r.status_code == 422 and r.data["type"] == "damaged", r.content


@pytest.mark.parametrize("first", ["close", "credit note"])
def test_a_paid_refused_damaged_stop_is_expensed_once_whichever_comes_first(api, w, first):
    _o, inv, t, dn = out(api, w)
    api.post("/api/v1/accounting/payments", {"direction": "in", "partner_type": "customer", "partner_id": str(w.cust.id), "amount": "750", "method": "cash",
                                             "allocations": [{"document_type": "sales_invoice", "document_id": inv["id"], "amount": "750"}]}, format="json")
    stop(api, dn, back="5", reason="rejected", condition="damaged")
    credit = {"customer": str(w.cust.id), "invoice": inv["id"], "post": True, "reason": "broken", "lines": [{"variant": str(w.v.id), "qty": "5", "unit_price": "150", "restock": False, "condition": "damaged"}]}
    if first == "close":
        close(api, t)
    assert api.post("/api/v1/credit-notes", credit, format="json").status_code == 201
    if first != "close":
        close(api, t)
    b = books(w)
    assert on_hand(w) == 95
    assert b["Stock in Hand"] == -500, "the 5 that left, once"
    assert "COGS" not in b
    assert b["Damaged Goods"] == 500, "500 of cost expensed, not 1000"


def test_a_paid_refused_stop_credited_with_its_trip_still_open_is_back_on_the_shelf_now(api, w):
    _o, inv, t, dn = out(api, w)
    api.post("/api/v1/accounting/payments", {"direction": "in", "partner_type": "customer", "partner_id": str(w.cust.id), "amount": "750", "method": "cash",
                                             "allocations": [{"document_type": "sales_invoice", "document_id": inv["id"], "amount": "750"}]}, format="json")
    assert api.post(f"/api/v1/delivery-notes/{dn['id']}/deliver", {"failed": True, "failure_reason": "Refused"}, format="json").status_code == 200
    r = api.post("/api/v1/credit-notes", {"customer": str(w.cust.id), "invoice": inv["id"], "post": True, "reason": "refused",
                                          "lines": [{"variant": str(w.v.id), "qty": "5", "unit_price": "150", "restock": True, "condition": "good"}]}, format="json")
    assert r.status_code == 201, r.content
    assert r.data["lines"][0]["stock_back"] is True
    assert on_hand(w) == 100, "TDN rarely closes a trip: the goods cannot wait for it"
    close(api, t)
    assert on_hand(w) == 100, "and the close does not count them again"


# ---------------------------------------------------------------- the reports

def _rows(key, **params):
    from apps.reports.registry import REPORTS

    return REPORTS[key]["query"](**params)


def test_the_returns_report_says_where_everything_went(api, w):
    # good and damaged at the door, one refused outright and rejected, one returned later
    o1, inv1, _t1, dn1 = out(api, w)
    stop(api, dn1)
    o2, inv2, _t2, dn2 = out(api, w)
    stop(api, dn2, reason="damaged", condition="damaged")
    o3, _inv3, _t3, dn3 = out(api, w)
    stop(api, dn3, back="5")
    assert api.post(f"/api/v1/sales-orders/{o3['id']}/customer-rejected", {"reason": "no"}, format="json").status_code == 200
    later = api.post("/api/v1/credit-notes", {"customer": str(w.cust.id), "invoice": inv1["id"], "post": True, "reason": "next week",
                                              "lines": [{"variant": str(w.v.id), "qty": "1", "unit_price": "150", "restock": False, "condition": "damaged"}]}, format="json").data
    with tenant_context(w.tenant):
        rep = _rows("returns_report")
    assert rep["columns"] == ["date", "customer", "item", "batch", "qty", "result", "reason", "cost_value", "credit_value", "order", "delivery_note", "invoice", "credit_note", "rep"]
    by = {(r["order"], r["credit_note"]): r for r in rep["rows"]}
    cn1 = api.get(f"/api/v1/delivery-notes/{dn1['id']}").data["credit_note"]["number"]
    good = by[(o1["number"], cn1)]
    assert (good["result"], D(good["qty"]), D(good["cost_value"]), D(good["credit_value"]), good["reason"]) == ("Back To Stock", D(1), D(100), D(150), "Rejected by customer")
    assert good["delivery_note"] == dn1["number"] and good["invoice"] == inv1["number"] and good["customer"] == "Salon One"
    cn2 = api.get(f"/api/v1/delivery-notes/{dn2['id']}").data["credit_note"]["number"]
    assert by[(o2["number"], cn2)]["result"] == "Wastage" and by[(o2["number"], cn2)]["reason"] == "Damaged"
    refused = by[(o3["number"], "")]
    assert (refused["result"], D(refused["qty"]), refused["reason"], D(refused["credit_value"])) == ("Back To Stock", D(5), "Customer rejected", D(0))
    after = by[(o1["number"], later["number"])]
    assert after["result"] == "Wastage" and after["reason"] == "next week"
    assert len(rep["rows"]) == 4, "each return once, none counted twice"
    t = rep["totals"]
    assert D(t["back_to_stock_qty"]) == 6 and D(t["wastage_qty"]) == 2 and D(t["back_to_stock_cost"]) == 600 and D(t["wastage_cost"]) == 200 and D(t["credit_value"]) == 450
    with tenant_context(w.tenant):
        assert {r["result"] for r in _rows("returns_report", result="Wastage")["rows"]} == {"Wastage"}
        assert _rows("returns_report", date_to="2000-01-01")["rows"] == []


def test_the_returns_report_reads_a_return_from_before_it_was_built(api, w):
    """TDN-SO-00149: returned at the stop, the trip closed, a credit note taken by hand afterwards. One row, not two."""
    from apps.delivery.models import DeliveryNoteLine

    o, inv, t, dn = out(api, w)
    assert stop(api, dn).status_code == 200
    with tenant_context(w.tenant, w.tenant.owner):
        from apps.sales.models import CreditNote

        cn = CreditNote.objects.get(invoice_id=inv["id"])
        CreditNote.objects.filter(pk=cn.pk).update(delivery_note=None)
        DeliveryNoteLine.objects.filter(delivery_note_id=dn["id"]).update(return_settled=False)
    close(api, t)
    with tenant_context(w.tenant):
        rows = _rows("returns_report")["rows"]
    assert len(rows) == 1 and rows[0]["credit_note"] == cn.number and rows[0]["result"] == "Back To Stock"


def test_wastage_counts_damaged_returns_as_well_as_adjustments(api, w):
    _o, _inv, _t, dn = out(api, w)
    stop(api, dn, reason="damaged", condition="damaged")
    _o2, _inv2, _t2, dn2 = out(api, w, invoice=None)
    stop(api, dn2, reason="damaged", condition="damaged")
    with tenant_context(w.tenant):
        rep = _rows("wastage_by_reason")
    by = {r["reason"]: D(r["value"]) for r in rep["rows"]}
    assert by["Damaged goods returned"] == 100 and by["Damaged"] == 100 and D(rep["totals"]["value"]) == 200


def test_sales_by_rep_shows_returns_so_the_columns_add_up(api, w):
    _o, inv, _t, dn = out(api, w)
    stop(api, dn, cash_collected="300")
    with tenant_context(w.tenant):
        rep = _rows("sales_by_rep")
    assert rep["columns"][:7] == ["label", "qty", "sales", "cash_sales", "credit_sales", "returns", "received"]
    r = rep["rows"][0]
    assert (D(r["sales"]), D(r["returns"]), D(r["received"]), D(r["outstanding"])) == (D(750), D(150), D(300), D(300))
    assert D(r["sales"]) - D(r["returns"]) - D(r["received"]) == D(r["outstanding"])
    assert D(rep["totals"]["returns"]) == 150


def test_stock_figures_stay_true(api, w):
    from apps.reports.operational import stock_report, stock_summary

    _o, _inv, _t, dn = out(api, w)
    with tenant_context(w.tenant):
        assert D(stock_summary()["rows"][0]["dispatched"]) == 5
    stop(api, dn)
    with tenant_context(w.tenant):
        assert D(stock_summary()["rows"][0]["dispatched"]) == 0, "the unit that came back is not still out"
        assert D(stock_report()["rows"][0]["closing_qty"]) == 96
    assert D(api.get("/api/v1/inventory/stock/summary").data[0]["dispatched"]) == 0, "nor on the Stock screen"
    assert api.post(f"/api/v1/delivery-notes/{dn['id']}/undo-return", {}, format="json").status_code == 200
    with tenant_context(w.tenant):
        assert D(stock_report()["rows"][0]["closing_qty"]) == on_hand(w) == 95, "an undone return comes off the returns"
        assert D(stock_summary()["rows"][0]["dispatched"]) == 0


def test_inventory_reconciliation_does_not_read_a_settled_unit_as_still_on_the_van(api, w):
    from apps.accounting.reports import inventory_reconciliation

    _o, _inv, _t, dn = out(api, w)
    stop(api, dn)
    with tenant_context(w.tenant):
        rec = inventory_reconciliation()
    on_van = next(r for r in rec["rows"] if "on van" in r["item"])
    assert D(on_van["amount"]) == 0


def test_the_stepper_says_how_many_came_back(api, w):
    o, _inv, _t, dn = out(api, w)
    stop(api, dn)
    steps = {s["key"]: s for s in api.get(f"/api/v1/sales-orders/{o['id']}/workflow").data["steps"]}
    assert steps["delivered"]["detail"].startswith("Partially delivered · 1 returned")
