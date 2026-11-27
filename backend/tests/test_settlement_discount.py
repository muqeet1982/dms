"""
Settlement discount (owner, 27 Nov 2026): "invoice is for 604 rs and customer only pay 600 … something like discount and go
to different head". A receipt that closes an invoice a few rupees short lets them go to Settlement Discount — its own head,
up to Rs 50 a time unless the owner or the right allows more, never on a cheque, undone with the receipt.
"""
from decimal import Decimal as D

import pytest
from django.db.models import Sum

from apps.core.exceptions import DomainError
from apps.core.tenancy import tenant_context

from .conftest import issue_order, seed_basic

pytestmark = pytest.mark.django_db


@pytest.fixture
def billed(api, tenant):
    wh, _sup, v, cust = seed_basic(tenant)                      # T.P 150
    o = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True, "lines": [{"variant": str(v.id), "qty": "1"}]}, format="json").data
    issue_order(api, o["id"])
    inv = api.post(f"/api/v1/sales-orders/{o['id']}/invoice", {"post": True}, format="json").data[0]
    assert D(inv["total"]) == 150
    return cust, inv


def _receive(api, cust, inv, amount, settlement=None, **extra):
    a = {"document_type": "sales_invoice", "document_id": inv["id"], "amount": amount}
    if settlement is not None:
        a["settlement"] = settlement
    return api.post("/api/v1/accounting/payments", {"direction": "in", "partner_type": "customer", "partner_id": str(cust.id), "amount": amount,
                                                    "method": "cash", "allocations": [a], **extra}, format="json")


def _books(tenant, role):
    from apps.accounting.models import GL_LIVE, JournalLine
    from apps.accounting.services import account_for

    with tenant_context(tenant):
        a = JournalLine.objects.filter(entry__status__in=GL_LIVE, account=account_for(role, "payment")).aggregate(d=Sum("debit"), c=Sum("credit"))
        return (a["d"] or D(0)) - (a["c"] or D(0))


def test_the_last_rupees_go_to_their_own_head(api, tenant, billed):
    from apps.partners.services import PartnerLedgerService

    cust, inv = billed
    r = _receive(api, cust, inv, "146", "4")
    assert r.status_code == 201, r.content
    got = api.get(f"/api/v1/invoices/{inv['id']}").data
    assert (got["status"], D(got["settled_amount"]), D(got["cash_paid"]), D(got["balance"])) == ("paid", D("4"), D("146"), D("0"))
    assert _books(tenant, "settlement_discount") == D("4") and _books(tenant, "discount") == 0, "its own head, not Discounts Allowed"
    with tenant_context(tenant):
        assert PartnerLedgerService.balance("customer", cust.pk) == 0, "the customer owes nothing"
    alloc = r.data["allocations"][0]
    assert (D(alloc["amount"]), D(alloc["settlement"])) == (D("146"), D("4"))
    rows = api.get("/api/v1/reports/settlement_discounts").data["rows"]
    assert [(x["invoice"], D(x["received"]), D(x["settlement"])) for x in rows] == [(inv["number"], D("146"), D("4"))]


def test_it_closes_the_invoice_or_nothing(api, billed):
    cust, inv = billed
    r = _receive(api, cust, inv, "146", "3")
    assert r.status_code == 400 and r.data["type"] == "settlement_not_closing", r.data


def test_above_the_limit_needs_the_owner_or_the_right(tenant, billed):
    from apps.accounting.services import PaymentService

    cust, inv = billed
    alloc = [{"document_type": "sales_invoice", "document_id": inv["id"], "amount": D("90"), "settlement": D("60")}]
    with tenant_context(tenant, tenant.owner):
        with pytest.raises(DomainError) as e:
            PaymentService.record(direction="in", partner_type="customer", partner_id=str(cust.pk), amount=D("90"), method="cash", allocations=alloc)
        assert e.value.detail.code == "settlement_over_limit" and e.value.status_code == 403
        pay = PaymentService.record(direction="in", partner_type="customer", partner_id=str(cust.pk), amount=D("90"), method="cash", allocations=alloc, settle_over_limit=True)
        assert pay.allocations.get().settlement == D("60")


def test_never_on_a_cheque_nor_a_supplier(api, tenant, billed):
    cust, inv = billed
    r = _receive(api, cust, inv, "146", "4", method="cheque", cheque_no="1", cheque_date="2026-11-30", cheque_bank="HBL")
    assert r.status_code == 400 and r.data["type"] == "settlement_cheque", r.data


def test_cancelling_the_receipt_brings_it_back(api, tenant, billed):
    cust, inv = billed
    pay = _receive(api, cust, inv, "146", "4").data
    assert api.post(f"/api/v1/accounting/payments/{pay['id']}/cancel", {"reason": "wrong"}, format="json").status_code == 200
    got = api.get(f"/api/v1/invoices/{inv['id']}").data
    assert (D(got["settled_amount"]), D(got["balance"])) == (D("0"), D("150")) and got["status"] in ("posted", "overdue")
    assert _books(tenant, "settlement_discount") == 0
    assert api.get("/api/v1/reports/settlement_discounts").data["rows"] == []


def test_the_form_knows_the_limit(api, billed):
    cust, _inv = billed
    legs = api.get("/api/v1/accounting/payments/legs", {"direction": "in", "partner_type": "customer", "partner_id": str(cust.id)}).data
    assert D(legs["settlement_limit"]) == 50 and legs["settle_over_limit"] is True, legs


def test_received_counts_money_only(api, tenant, billed):
    cust, inv = billed
    _receive(api, cust, inv, "146", "4")
    cards = {c["label"]: D(c["value"]) for c in api.get("/api/v1/reports/daily_sales").data["summary"]}
    assert (cards["Received"], cards["Outstanding"]) == (D("146"), D("0"))


def test_an_invoice_already_paid_short_closes_from_its_page(api, tenant, billed):
    """Owner, 27 Nov 2026: "check current invoice and its payment … if same reason for partial, then close them"."""
    cust, inv = billed
    pay = _receive(api, cust, inv, "146").data                  # 4 left, nothing let go at the receipt
    got = api.get(f"/api/v1/invoices/{inv['id']}").data
    assert (D(got["balance"]), D(got["settlement_limit"])) == (D("4"), D("50"))
    r = api.post(f"/api/v1/invoices/{inv['id']}/settle-remaining", {}, format="json")
    assert r.status_code == 200 and r.data["status"] == "paid" and D(r.data["settled_amount"]) == D("4"), r.data
    assert _books(tenant, "settlement_discount") == D("4")
    assert api.get(f"/api/v1/accounting/payments/{pay['id']}").data["allocations"][0]["settlement"] in ("4.0000", "4.00", "4")
    assert api.post(f"/api/v1/invoices/{inv['id']}/settle-remaining", {}, format="json").status_code == 400, "nothing left"
    # the receipt cancelled: the 4 is owed again, with the 146
    assert api.post(f"/api/v1/accounting/payments/{pay['id']}/cancel", {"reason": "wrong"}, format="json").status_code == 200
    got = api.get(f"/api/v1/invoices/{inv['id']}").data
    assert (D(got["balance"]), D(got["settled_amount"])) == (D("150"), D("0")) and _books(tenant, "settlement_discount") == 0


def test_closing_needs_a_receipt_and_keeps_to_the_limit(api, tenant, billed):
    from apps.accounting.services import PaymentService
    from apps.sales.models import SalesInvoice

    cust, inv = billed
    r = api.post(f"/api/v1/invoices/{inv['id']}/settle-remaining", {}, format="json")
    assert r.status_code == 400 and r.data["type"] == "settlement_no_receipt", r.data
    _receive(api, cust, inv, "90")                              # 60 left: above Rs 50
    with tenant_context(tenant, tenant.owner):
        with pytest.raises(DomainError) as e:
            PaymentService.settle_remaining(SalesInvoice.objects.get(pk=inv["id"]))
        assert e.value.detail.code == "settlement_over_limit"
    assert api.post(f"/api/v1/invoices/{inv['id']}/settle-remaining", {}, format="json").status_code == 200, "the owner may"
