"""
Owner, 24 Nov 2026: "what much pending and how much cash or credit pending ... which payments are due in next 7 days with
option to change to 14 or 30". And a Credit sale to a customer with no credit days is due after the company's default (30).
"""
from decimal import Decimal as D

import pytest
from django.utils import timezone

from apps.core.tenancy import tenant_context

from .conftest import issue_order, seed_basic

pytestmark = pytest.mark.django_db


def _bill(api, wh, cust, v, qty, mode):
    o = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True, "payment_mode": mode,
                                          "lines": [{"variant": str(v.id), "qty": qty}]}, format="json").data
    issue_order(api, o["id"])
    return api.post(f"/api/v1/sales-orders/{o['id']}/invoice", {"post": True}, format="json").data[0]


@pytest.fixture
def owed(api, tenant):
    wh, _s, v, cust = seed_basic(tenant)                      # 0 credit days, T.P 150
    cash = _bill(api, wh, cust, v, "2", "cash")               # 300, due today
    credit = _bill(api, wh, cust, v, "1", "credit")           # 150, due in 30 days
    old = _bill(api, wh, cust, v, "3", "cash")                # 450, made overdue below
    paid = _bill(api, wh, cust, v, "1", "cash")               # paid in full: not pending
    from apps.accounting.services import PaymentService
    from apps.sales.models import SalesInvoice

    with tenant_context(tenant, tenant.owner):
        SalesInvoice.objects.filter(pk=old["id"]).update(due_date=timezone.localdate() - timezone.timedelta(days=5))
        PaymentService.record(direction="in", partner_type="customer", partner_id=str(cust.id), partner_name=cust.name, amount=D("150"), method="cash",
                              allocations=[{"document_type": "sales_invoice", "document_id": paid["id"], "amount": D("150")}])
    return cash, credit, old


def test_a_credit_sale_gets_the_default_days_and_cash_is_due_today(api, owed):
    cash, credit, _old = owed
    today = timezone.localdate()
    assert cash["due_date"] == today.isoformat()
    assert credit["due_date"] == (today + timezone.timedelta(days=30)).isoformat()


def test_pending_payments(api, owed):
    cash, credit, old = owed
    data = api.get("/api/v1/reports/pending_payments").data                      # opens on the next 7 days
    cards = {c["label"]: c for c in data["summary"]}
    assert D(cards["Total pending"]["value"]) == D("900") and cards["Total pending"]["count"] == 3
    assert D(cards["Cash pending"]["value"]) == D("750") and D(cards["Credit pending"]["value"]) == D("150")
    assert D(cards["Overdue"]["value"]) == D("450") and D(cards["Due in next 7 days"]["value"]) == D("300")
    assert [r["invoice"] for r in data["rows"]] == [cash["number"]] and data["rows"][0]["when"] == "Due today"
    assert data["column_labels"]["pay_type"] == "Cash / Credit" and data["links"]["view"]["id"] == "invoice_id"
    nxt30 = api.get("/api/v1/reports/pending_payments", {"due_within": "Next 30 days"}).data
    assert {r["invoice"] for r in nxt30["rows"]} == {cash["number"], credit["number"]}
    assert D(nxt30["totals"]["cash_due"]) == D("300") and D(nxt30["totals"]["credit_due"]) == D("150") and D(nxt30["totals"]["due"]) == D("450")
    over = api.get("/api/v1/reports/pending_payments", {"due_within": "Overdue"}).data["rows"]
    assert [(r["invoice"], r["when"]) for r in over] == [(old["number"], "5 days overdue")]
    every = api.get("/api/v1/reports/pending_payments", {"due_within": "All pending", "pay_type": "Credit"}).data
    assert [r["invoice"] for r in every["rows"]] == [credit["number"]] and D(every["summary"][0]["value"]) == D("150"), "the cards follow the cash/credit filter"
    for f in ("xlsx", "pdf"):
        assert api.get("/api/v1/reports/pending_payments", {"due_within": "All pending", "format": f}).status_code == 200
