"""
Returns, every case (owner, 30 Oct 2026: "do research and make plan … make sure update stock, inventory and
accounting … give multiple options for customer amount return"):

A  the customer refuses the goods, nothing paid  → Customer rejected: no return, no payment, goods back once
B  goods delivered then returned, unpaid / partly paid / fully paid, all or part → a return that credits exactly
   what was charged (discount and the manufacturer's share mirrored), settles the invoice as CREDIT (Returned,
   never Paid), and leaves the rest as customer credit: kept for the next invoice, applied now, or refunded.
"""
from decimal import Decimal

import pytest

from apps.core.tenancy import tenant_context

pytestmark = pytest.mark.django_db
D = Decimal


@pytest.fixture
def shop(api, tenant):
    """A 10%-discount customer whose discount Keune co-funds (4 of the 10 points), 150 a unit."""
    from apps.sales.models import DiscountContribution

    from .conftest import seed_basic

    wh, sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        DiscountContribution.objects.get_or_create(total_pct=D("10"), defaults={"distributor_pct": D("6"), "company_pct": D("4")})
        cust.default_discount_pct = D("10")
        cust.save(update_fields=["default_discount_pct"])
    return wh, sup, v, cust


def _on_hand(tenant, wh, v):
    from apps.inventory.models import StockLevel

    with tenant_context(tenant):
        return sum((s.qty_on_hand for s in StockLevel.objects.filter(warehouse=wh, variant=v)), D("0"))


def _issued_invoice(api, wh, cust, v, qty="2", post=True):
    o = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True, "lines": [{"variant": str(v.id), "qty": qty}]}, format="json").data
    pl = api.post(f"/api/v1/sales-orders/{o['id']}/load-form", {"send": True}, format="json").data
    api.post(f"/api/v1/pick-lists/{pl['id']}/issue", {"issues": [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in pl["lines"]]}, format="json")
    inv = api.post(f"/api/v1/sales-orders/{o['id']}/invoice", {"post": post}, format="json").data[0]
    return o, inv


def _delivered_invoice(api, wh, cust, v, qty="2"):
    o, inv = _issued_invoice(api, wh, cust, v, qty)
    dn = api.get(f"/api/v1/invoices/{inv['id']}").data["delivery_note"]
    assert api.post(f"/api/v1/delivery-notes/{dn}/deliver", {"receiver_name": "Shop"}, format="json").status_code == 200
    return o, inv


def _return(api, inv, qty, post=True, settlement=None, **line):
    full = api.get(f"/api/v1/invoices/{inv['id']}").data
    l0 = full["lines"][0]
    body = {"customer": full["customer"], "invoice": inv["id"], "reason": "returned", "post": post,
            "lines": [{"variant": str(l0["variant"]), "qty": str(qty), "unit_price": str(l0["unit_price"]), "restock": True, "condition": "good", **line}]}
    if settlement is not None:
        body["settlement"] = settlement
    return api.post("/api/v1/credit-notes", body, format="json")


def _gl(tenant, cn_id):
    from apps.accounting.models import JournalEntry

    with tenant_context(tenant):
        out = {}
        for je in JournalEntry.objects.filter(source_type="CreditNote", source_id=cn_id, status="posted"):
            assert sum(l.debit for l in je.lines.all()) == sum(l.credit for l in je.lines.all()), je.memo
            for l in je.lines.select_related("account"):
                out[l.account.name] = out.get(l.account.name, D("0")) + l.debit - l.credit
        return out


def test_case_a_customer_rejected_reverses_the_sale_with_no_return_and_no_payment(api, tenant, shop):
    from apps.accounting.models import JournalEntry

    wh, _sup, v, cust = shop
    before = _on_hand(tenant, wh, v)
    o, inv = _issued_invoice(api, wh, cust, v)
    assert _on_hand(tenant, wh, v) == before - 2
    r = api.post(f"/api/v1/invoices/{inv['id']}/reject", {"reason": "shop closed"}, format="json")
    assert r.status_code == 200, r.content
    assert r.data["status"] == "cancelled" and D(r.data["paid_amount"]) == 0, "no payment, no return"
    assert api.get(f"/api/v1/sales-orders/{o['id']}").data["status"] == "rejected"
    assert _on_hand(tenant, wh, v) == before, "the goods back once"
    with tenant_context(tenant):
        assert not JournalEntry.objects.filter(source_type="SalesInvoice", source_id=inv["id"], status="posted").exists(), "the sale is off the books"
    assert api.get("/api/v1/credit-notes", {"invoice": inv["id"]}).data["results"] == []
    assert D(api.get(f"/api/v1/customers/{cust.id}").data["outstanding"]) == 0


def test_cancelling_an_order_takes_its_unpaid_invoice_and_a_return_cannot_count_the_goods_twice(api, tenant, shop):
    wh, _sup, v, cust = shop
    before = _on_hand(tenant, wh, v)
    o, inv = _issued_invoice(api, wh, cust, v)
    assert api.post(f"/api/v1/sales-orders/{o['id']}/cancel", {"reason": "customer changed mind"}, format="json").status_code == 200
    assert api.get(f"/api/v1/invoices/{inv['id']}").data["status"] == "cancelled", "the invoice goes with its order"
    assert _on_hand(tenant, wh, v) == before

    o2, inv2 = _issued_invoice(api, wh, cust, v)
    with tenant_context(tenant):
        from apps.sales.models import SalesOrder

        SalesOrder.objects.filter(pk=o2["id"]).update(status="cancelled")   # the TDN-SO-00076 state: cancelled, invoice left live
    r = _return(api, inv2, 2)
    assert r.status_code in (400, 422) and "Customer rejected" in str(r.content), "the goods already came back"


def test_case_b_a_full_return_credits_what_was_charged_and_reads_returned_not_paid(api, tenant, shop):
    wh, _sup, v, cust = shop
    before = _on_hand(tenant, wh, v)
    _o, inv = _delivered_invoice(api, wh, cust, v)                     # 2 x 150 = 300 less 10% = 270
    assert D(inv["total"]) == D("270")
    r = _return(api, inv, 2)
    assert r.status_code == 201, r.content
    cn = r.data
    assert D(cn["total"]) == D("270") and D(cn["discount"]) == D("30"), "credited what was charged, not the list price"
    i = api.get(f"/api/v1/invoices/{inv['id']}").data
    assert i["status"] == "returned" and D(i["cash_paid"]) == 0 and D(i["credited_amount"]) == D("270") and D(i["balance"]) == 0
    assert _on_hand(tenant, wh, v) == before, "restocked"
    gl = _gl(tenant, cn["id"])
    assert gl["Sales Returns"] == D("300")
    assert gl.get("Discounts Allowed", 0) == D("-18"), "our own 6 points back"
    assert gl.get("Scheme / Incentive from Manufacturer", 0) == D("-12") + D("12"), "Keune's 4 points back, and its claim clawed back"
    assert D(api.get(f"/api/v1/customers/{cust.id}").data["outstanding"]) == 0


def test_case_b_a_partial_return_leaves_the_rest_to_pay_and_nothing_can_be_returned_twice(api, tenant, shop):
    wh, _sup, v, cust = shop
    _o, inv = _delivered_invoice(api, wh, cust, v)
    assert _return(api, inv, 1).status_code == 201
    i = api.get(f"/api/v1/invoices/{inv['id']}").data
    assert i["status"] == "posted" and D(i["balance"]) == D("135") and D(i["cash_paid"]) == 0
    over = _return(api, inv, 2)
    assert over.status_code in (400, 422) and "only 1 of" in str(over.content)


def test_partly_paid_the_return_clears_the_rest_and_the_excess_is_refunded_or_kept_for_the_next_invoice(api, tenant, shop):
    wh, _sup, v, cust = shop
    _o, inv = _delivered_invoice(api, wh, cust, v)                      # 270
    api.post("/api/v1/accounting/payments", {"direction": "in", "partner_type": "customer", "partner_id": str(cust.id), "amount": "200", "method": "cash",
                                             "allocations": [{"document_type": "sales_invoice", "document_id": inv["id"], "amount": "200"}]}, format="json")
    r = _return(api, inv, 2, settlement={"refund": {"amount": "150", "method": "cash"}})
    assert r.status_code == 201, r.content
    cn = api.get(f"/api/v1/credit-notes/{r.data['id']}").data
    i = api.get(f"/api/v1/invoices/{inv['id']}").data
    assert i["status"] == "paid" and D(i["cash_paid"]) == D("200") and D(i["credited_amount"]) == D("70"), "70 of the credit cleared the rest"
    assert D(cn["refunded_amount"]) == D("150") and D(cn["available_credit"]) == D("50") and cn["refunds"][0]["method"] == "cash"
    assert D(api.get(f"/api/v1/customers/{cust.id}").data["available_credit"]) == D("50")

    _o2, nxt = _delivered_invoice(api, wh, cust, v, qty="1")            # 135: the kept 50 settles part of it
    n = api.get(f"/api/v1/invoices/{nxt['id']}").data
    assert D(n["credited_amount"]) == D("50") and D(n["balance"]) == D("85") and n["status"] == "posted"
    assert D(api.get(f"/api/v1/customers/{cust.id}").data["available_credit"]) == 0


def test_fully_paid_a_partial_return_is_credit_to_apply_elsewhere_or_refund_by_bank(api, tenant, shop):
    from apps.accounting.models import Account, BankAccount

    wh, _sup, v, cust = shop
    _o, inv = _delivered_invoice(api, wh, cust, v)
    api.post("/api/v1/accounting/payments", {"direction": "in", "partner_type": "customer", "partner_id": str(cust.id), "amount": "270", "method": "cash",
                                             "allocations": [{"document_type": "sales_invoice", "document_id": inv["id"], "amount": "270"}]}, format="json")
    _o2, other = _issued_invoice(api, wh, cust, v, qty="1")            # an open 135 elsewhere — no kept credit yet
    cn = _return(api, inv, 2).data                                      # 270 of credit: the invoice was already paid
    assert api.get(f"/api/v1/invoices/{inv['id']}").data["status"] == "paid"
    assert D(api.get(f"/api/v1/credit-notes/{cn['id']}").data["available_credit"]) == D("270")
    a = api.post(f"/api/v1/credit-notes/{cn['id']}/apply", {"allocations": [{"invoice": other["id"], "amount": "135"}]}, format="json")
    assert a.status_code == 200, a.content
    assert api.get(f"/api/v1/invoices/{other['id']}").data["status"] == "returned"
    with tenant_context(tenant, tenant.owner):
        bank = BankAccount.objects.create(name="Refunds", bank="HBL", branch="Main", account_no="1", account=Account.objects.filter(type="asset", is_group=False).first())
    rf = api.post(f"/api/v1/credit-notes/{cn['id']}/refund", {"amount": "135", "method": "bank_transfer", "bank_account": str(bank.id)}, format="json")
    assert rf.status_code == 200, rf.content
    assert D(rf.data["available_credit"]) == 0 and rf.data["refunds"][0]["method"] == "bank_transfer"
    too_much = api.post(f"/api/v1/credit-notes/{cn['id']}/refund", {"amount": "1", "method": "cash"}, format="json")
    assert too_much.status_code in (400, 422)


def _trip_invoice(api, tenant, wh, cust, v, qty="3"):
    """Invoice-before-dispatch on a trip: the trip is opened, the load form issued onto its note, the invoice posted."""
    from apps.delivery.models import Driver, Vehicle

    with tenant_context(tenant, tenant.owner):
        driver = Driver.objects.create(name="Akram", phone="03001234567")
        vehicle = Vehicle.objects.create(reg_no=f"LEB-{qty}{driver.pk.hex[:4]}")
    o = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True, "lines": [{"variant": str(v.id), "qty": qty}]}, format="json").data
    trip = api.post("/api/v1/trips", {"warehouse": str(wh.id), "driver": str(driver.id), "vehicle": str(vehicle.id), "delivery_rep": str(tenant.owner.id), "order_ids": [o["id"]]}, format="json").data
    pl = api.post(f"/api/v1/sales-orders/{o['id']}/load-form", {"send": True}, format="json").data
    api.post(f"/api/v1/pick-lists/{pl['id']}/issue", {"issues": [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in pl["lines"]]}, format="json")
    inv = api.post(f"/api/v1/sales-orders/{o['id']}/invoice", {"post": True}, format="json").data[0]
    assert api.post(f"/api/v1/trips/{trip['id']}/start", {}, format="json").status_code == 200
    return o, inv, trip


def test_goods_refused_at_the_door_come_home_with_the_trip_and_the_return_moves_no_stock_again(api, tenant, shop):
    wh, _sup, v, cust = shop
    before = _on_hand(tenant, wh, v)
    _o, inv, trip = _trip_invoice(api, tenant, wh, cust, v, qty="3")       # billed 3 at issue
    dn = api.get(f"/api/v1/invoices/{inv['id']}").data["delivery_note"]
    early = _return(api, inv, 1)
    assert early.status_code in (400, 422) and "not been delivered" in str(early.content), "no return while the goods are on the van"
    line = api.get(f"/api/v1/delivery-notes/{dn}").data["lines"][0]
    r = api.post(f"/api/v1/delivery-notes/{dn}/deliver", {"receiver_name": "Shop", "delivered": [{"line_id": line["id"], "qty_delivered": "2", "qty_returned": "1"}]}, format="json")
    assert r.status_code == 200, r.content
    assert api.post(f"/api/v1/trips/{trip['id']}/close", {"override": True}, format="json").status_code == 200
    assert _on_hand(tenant, wh, v) == before - 2, "the trip brought the refused unit home"
    cn = _return(api, inv, 1)
    assert cn.status_code == 201, cn.content
    assert cn.data["lines"][0]["stock_back"] is True
    assert _on_hand(tenant, wh, v) == before - 2, "…and the return does not count it again"
    assert _gl(tenant, cn.data["id"]).get("Stock in Hand", 0) > 0, "its cost goes back to Stock in Hand"
    i = api.get(f"/api/v1/invoices/{inv['id']}").data
    assert D(i["balance"]) == D("270") and D(i["credited_amount"]) == D("135"), "the refused unit is credited; the two kept are owed"


def test_with_no_trip_nothing_brings_refused_goods_home_so_the_return_restocks_them(api, tenant, shop):
    wh, _sup, v, cust = shop
    before = _on_hand(tenant, wh, v)
    _o, inv = _issued_invoice(api, wh, cust, v, qty="3")
    dn = api.get(f"/api/v1/invoices/{inv['id']}").data["delivery_note"]
    line = api.get(f"/api/v1/delivery-notes/{dn}").data["lines"][0]
    api.post(f"/api/v1/delivery-notes/{dn}/deliver", {"receiver_name": "Shop", "delivered": [{"line_id": line["id"], "qty_delivered": "2", "qty_returned": "1"}]}, format="json")
    cn = _return(api, inv, 1)
    assert cn.status_code == 201, cn.content
    assert cn.data["lines"][0]["stock_back"] is False
    assert _on_hand(tenant, wh, v) == before - 2


def test_a_draft_return_blocks_reversing_its_invoice_until_deleted_and_a_stale_draft_cannot_post(api, tenant, shop):
    wh, _sup, v, cust = shop
    _o, inv = _delivered_invoice(api, wh, cust, v)
    draft = _return(api, inv, 1, post=False).data
    blocked = api.post(f"/api/v1/invoices/{inv['id']}/reopen", {"reason": "edit"}, format="json")
    assert blocked.status_code in (400, 422) and "drafted" in str(blocked.content), "a draft could post after the sale is gone"
    held = _return(api, inv, 2)
    assert held.status_code in (400, 422) and "only 1 of" in str(held.content), "the draft holds its unit"
    assert api.delete(f"/api/v1/credit-notes/{draft['id']}").status_code == 204
    assert _return(api, inv, 2).status_code == 201, "deleted, the draft's unit is free again"
    posted = api.get("/api/v1/credit-notes", {"invoice": inv["id"]}).data["results"][0]
    assert api.delete(f"/api/v1/credit-notes/{posted['id']}").status_code in (400, 422), "a posted return stays"
    other = api.post("/api/v1/customers", {"name": "Other Shop", "type": "retailer", "phone": "03001112222", "cnic": "3520212345671"}, format="json").data
    moved = api.patch(f"/api/v1/credit-notes/{posted['id']}", {"customer": other["id"]}, format="json")
    assert moved.status_code in (400, 422), "a posted return cannot move to another customer"


def test_cancelling_a_refund_gives_the_credit_back(api, tenant, shop):
    wh, _sup, v, cust = shop
    _o, inv = _delivered_invoice(api, wh, cust, v)
    api.post("/api/v1/accounting/payments", {"direction": "in", "partner_type": "customer", "partner_id": str(cust.id), "amount": "270", "method": "cash",
                                             "allocations": [{"document_type": "sales_invoice", "document_id": inv["id"], "amount": "270"}]}, format="json")
    cn = _return(api, inv, 1, settlement={"choice": "refund", "refund": {"method": "cash", "amount": "100"}}).data
    cn = api.get(f"/api/v1/credit-notes/{cn['id']}").data
    assert D(cn["refunded_amount"]) == D("100") and D(cn["available_credit"]) == D("35")
    assert api.post(f"/api/v1/accounting/payments/{cn['refunds'][0]['id']}/cancel", {"reason": "not paid"}, format="json").status_code == 200
    cn = api.get(f"/api/v1/credit-notes/{cn['id']}").data
    assert D(cn["refunded_amount"]) == 0 and D(cn["available_credit"]) == D("135")


def test_kept_credit_taken_by_an_invoice_at_issue_goes_back_if_the_customer_rejects_it(api, tenant, shop):
    wh, _sup, v, cust = shop
    _o, inv = _delivered_invoice(api, wh, cust, v)
    api.post("/api/v1/accounting/payments", {"direction": "in", "partner_type": "customer", "partner_id": str(cust.id), "amount": "270", "method": "cash",
                                             "allocations": [{"document_type": "sales_invoice", "document_id": inv["id"], "amount": "270"}]}, format="json")
    cn = _return(api, inv, 1).data                                          # 135 kept as credit
    o2, nxt = _issued_invoice(api, wh, cust, v, qty="1")                  # posted at issue: takes the 135
    assert D(api.get(f"/api/v1/invoices/{nxt['id']}").data["credited_amount"]) == D("135")
    r = api.post(f"/api/v1/invoices/{nxt['id']}/reject", {"reason": "refused"}, format="json")
    assert r.status_code == 200, r.content
    assert D(api.get(f"/api/v1/credit-notes/{cn['id']}").data["available_credit"]) == D("135"), "the credit is theirs again"
    assert D(api.get(f"/api/v1/customers/{cust.id}").data["available_credit"]) == D("135")


def test_three_one_unit_returns_settle_the_invoice_to_the_paisa(api, tenant, shop):
    from apps.sales.models import SalesInvoice, SalesInvoiceLine

    wh, _sup, v, cust = shop
    _o, inv = _delivered_invoice(api, wh, cust, v, qty="3")                # 405
    with tenant_context(tenant):                                          # a tax that does not split into thirds
        SalesInvoiceLine.objects.filter(invoice_id=inv["id"]).update(tax1_amount=D("48.10"), tax1_rate=D("18"))
        i = SalesInvoice.objects.get(pk=inv["id"])
        SalesInvoice.objects.filter(pk=i.pk).update(tax1_total=D("48.10"), total=i.total + D("48.10"))
    taxes = [D(_return(api, inv, 1).data["tax1_total"]) for _ in range(3)]
    assert sum(taxes) == D("48.10"), taxes
    i = api.get(f"/api/v1/invoices/{inv['id']}").data
    assert D(i["balance"]) == 0 and i["status"] == "returned"


def test_the_tax_register_shows_a_return_after_its_discount(api, tenant, shop):
    from apps.reports.operational import sales_tax_register

    from apps.sales.models import SalesInvoice, SalesInvoiceLine

    wh, _sup, v, cust = shop
    _o, inv = _delivered_invoice(api, wh, cust, v)
    with tenant_context(tenant):                                          # a taxed invoice: 18% on the 270 charged
        SalesInvoiceLine.objects.filter(invoice_id=inv["id"]).update(tax1_amount=D("48.60"), tax1_rate=D("18"))
        i = SalesInvoice.objects.get(pk=inv["id"])
        SalesInvoice.objects.filter(pk=i.pk).update(tax1_total=D("48.60"), total=i.total + D("48.60"))
    cn = _return(api, inv, 2).data
    with tenant_context(tenant):
        rows = sales_tax_register()["rows"]
    row = next(r for r in rows if r["number"] == cn["number"])
    assert D(row["taxable_value"]) == D("-270")


def test_reversing_a_sale_needs_the_orders_cancel_rights(api, tenant, shop):
    from rest_framework.test import APIClient

    from apps.accounts.models import User

    wh, _sup, v, cust = shop
    _o, inv = _issued_invoice(api, wh, cust, v)
    role = api.post("/api/v1/roles", {"name": "Invoices only", "role_type": "internal", "permissions": ["sales.view_invoice", "sales.delete_invoice"]}, format="json").data
    api.post("/api/v1/users", {"full_name": "Clerk", "email": "clerk@probe.pk", "password": "Abcd-1234!", "roles": [role["id"]]}, format="json")
    User.objects.filter(email="clerk@probe.pk").update(must_change_password=False)
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {c.post('/api/v1/auth/login', {'identifier': 'clerk@probe.pk', 'password': 'Abcd-1234!'}, format='json').data['access']}", HTTP_X_TENANT_ID=str(tenant.id))
    assert c.post(f"/api/v1/invoices/{inv['id']}/reject", {"reason": "x"}, format="json").status_code == 403


def test_a_claim_the_manufacturer_already_paid_blocks_reversing_the_sale(api, tenant, shop):
    """Reversing would drop the claim's entries while the manufacturer's receipt stays — receivable off by it."""
    from apps.sales.models import SalesInvoice
    from apps.sales.services import CompanyClaimService

    wh, _sup, v, cust = shop
    _o, inv = _issued_invoice(api, wh, cust, v)
    with tenant_context(tenant, tenant.owner):
        claim = SalesInvoice.objects.get(pk=inv["id"]).company_claims.get()
        CompanyClaimService.settle_receipt(claim, method="cash")
    r = api.post(f"/api/v1/invoices/{inv['id']}/reject", {"reason": "refused"}, format="json")
    assert r.status_code in (400, 422) and "settled by a payment" in str(r.content)
    assert api.get(f"/api/v1/invoices/{inv['id']}").data["status"] == "posted", "nothing changed"


def test_reversing_an_invoice_gives_back_every_application_of_the_same_credit(api, tenant, shop):
    """Two applications of one return on one invoice both come off it (review, 30 Oct 2026: the second save used
    to overwrite the first and the customer lost credit)."""
    wh, _sup, v, cust = shop
    _o, inv = _delivered_invoice(api, wh, cust, v)
    api.post("/api/v1/accounting/payments", {"direction": "in", "partner_type": "customer", "partner_id": str(cust.id), "amount": "270", "method": "cash",
                                             "allocations": [{"document_type": "sales_invoice", "document_id": inv["id"], "amount": "270"}]}, format="json")
    cn = _return(api, inv, 2).data                                          # 270 kept as credit
    _oa, inv_a = _issued_invoice(api, wh, cust, v, qty="1")                 # takes 135
    _ob, inv_b = _issued_invoice(api, wh, cust, v, qty="2")                 # takes the other 135 of its 270
    assert api.post(f"/api/v1/invoices/{inv_a['id']}/reject", {"reason": "refused"}, format="json").status_code == 200
    assert api.post(f"/api/v1/credit-notes/{cn['id']}/apply", {"allocations": [{"invoice": inv_b["id"], "amount": "135"}]}, format="json").status_code == 200
    b = api.get(f"/api/v1/invoices/{inv_b['id']}").data
    assert b["status"] == "returned" and len(b["credit_applications"]) == 2 and not any(a["own"] for a in b["credit_applications"])
    assert b["returns_count"] == 0 and api.get(f"/api/v1/invoices/{inv['id']}").data["returns_count"] == 1
    assert api.post(f"/api/v1/invoices/{inv_b['id']}/reject", {"reason": "refused"}, format="json").status_code == 200
    assert D(api.get(f"/api/v1/credit-notes/{cn['id']}").data["available_credit"]) == D("270"), "all of it is theirs again"
    assert D(api.get(f"/api/v1/customers/{cust.id}").data["available_credit"]) == D("270")


def test_a_refund_cheque_is_undone_once_whether_it_bounces_or_is_cancelled(api, tenant, shop):
    from apps.accounting.models import GL_LIVE, BankAccount, JournalLine
    from apps.accounting.services import account_for

    wh, _sup, v, cust = shop
    _o, inv = _delivered_invoice(api, wh, cust, v)
    api.post("/api/v1/accounting/payments", {"direction": "in", "partner_type": "customer", "partner_id": str(cust.id), "amount": "270", "method": "cash",
                                             "allocations": [{"document_type": "sales_invoice", "document_id": inv["id"], "amount": "270"}]}, format="json")
    cn = _return(api, inv, 2).data                                          # 270 kept as credit
    with tenant_context(tenant, tenant.owner):
        gl_bank = account_for("bank", "payment")
        bank = BankAccount.objects.filter(account=gl_bank).first() or BankAccount.objects.create(name="Refunds", bank="HBL", branch="Main", account_no="1", account=gl_bank)
        bank_gl, payable = bank.account_id, account_for("cheques_payable", "payment").pk

    def gl(**f):
        with tenant_context(tenant):
            return sum((l.debit - l.credit for l in JournalLine.objects.filter(entry__status__in=GL_LIVE, **f)), D("0"))

    def refund(amount, method="cheque"):
        before = {p["id"] for p in api.get(f"/api/v1/credit-notes/{cn['id']}").data["refunds"]}
        r = api.post(f"/api/v1/credit-notes/{cn['id']}/refund", {"amount": amount, "method": method, "bank_account": str(bank.id), "cheque_no": "7001"}, format="json")
        assert r.status_code == 200, r.content
        (new,) = [p["id"] for p in r.data["refunds"] if p["id"] not in before]
        return new

    def left():
        return D(api.get(f"/api/v1/credit-notes/{cn['id']}").data["available_credit"])

    bounced = refund("100")
    refund("100", "cash")
    assert left() == D("70")
    assert api.post(f"/api/v1/accounting/payments/{bounced}/cheque/bounced", {}, format="json").status_code == 200
    assert left() == D("170")
    ar = gl(partner_type="customer", partner_id=str(cust.id))
    assert api.post(f"/api/v1/accounting/payments/{bounced}/cancel", {"reason": "bounced"}, format="json").status_code == 200
    assert left() == D("170"), "the bounce already gave it back"
    assert gl(partner_type="customer", partner_id=str(cust.id)) == ar, "and booked it once"

    cleared = refund("50")
    assert gl(account_id=bank_gl) == 0, "the cash refund came out of cash, though a bank was named"
    bank_before = gl(account_id=bank_gl)                                   # an unpresented cheque has not moved the bank
    c = api.post(f"/api/v1/accounting/payments/{cleared}/cheque/cleared", {}, format="json")
    assert c.status_code == 200, c.content
    assert gl(account_id=bank_gl) == bank_before - D("50")
    assert api.post(f"/api/v1/accounting/payments/{cleared}/cancel", {"reason": "wrong customer"}, format="json").status_code == 200
    assert left() == D("170") and gl(account_id=bank_gl) == bank_before and gl(account_id=payable) == 0
    late = api.post(f"/api/v1/accounting/payments/{cleared}/cheque/bounced", {}, format="json")
    assert late.status_code == 422 and "cancelled" in late.data["detail"] and left() == D("170")
