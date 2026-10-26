"""
Owner, 27 Oct 2026: "when create sale order, load form and invoice autocreating … i need to create load form and
save it … i want to see or edit before create invoice and same for other steps as well."

Nothing in the chain finalises itself any more: a load form is SAVED (open) and corrected before Send; an invoice
from the create page is a DRAFT until Post to ledger; goods delivered with no invoice are billed as a draft, and
cash collected against it waits on the customer's account until the invoice is posted.
"""
from decimal import Decimal

import pytest

from apps.core.tenancy import tenant_context

pytestmark = pytest.mark.django_db
D = Decimal


@pytest.fixture
def confirmed(api, tenant):
    from .conftest import seed_basic

    wh, _sup, v, cust = seed_basic(tenant)
    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True,
                                          "lines": [{"variant": str(v.id), "qty": "5"}]}, format="json")
    assert r.status_code == 201 and r.data["status"] == "confirmed", r.content
    return r.data, wh, v, cust


def _steps(api, oid):
    return {s["key"]: s for s in api.get(f"/api/v1/sales-orders/{oid}/workflow").data["steps"]}


def test_the_load_form_is_saved_corrected_and_only_then_sent(api, confirmed):
    order, wh, v, cust = confirmed
    pl = api.post(f"/api/v1/sales-orders/{order['id']}/load-form", {}, format="json")
    assert pl.status_code == 201, pl.content
    assert pl.data["status"] == "open" and pl.data["sent_at"] is None, "created, not sent to the warehouse"
    step = _steps(api, order["id"])["load_form"]
    assert step["state"] == "current" and step["action"]["label"] == "Review & send"
    assert _steps(api, order["id"])["load_form"]["action"]["to"].endswith(pl.data["id"])

    line = pl.data["lines"][0]
    too_much = api.post(f"/api/v1/pick-lists/{pl.data['id']}/lines", {"lines": [{"line_id": line["id"], "qty_to_pick": "9"}]}, format="json")
    assert too_much.status_code == 422 and "pending" in str(too_much.content)
    ok = api.post(f"/api/v1/pick-lists/{pl.data['id']}/lines", {"lines": [{"line_id": line["id"], "qty_to_pick": "3"}], "notes": "fragile"}, format="json")
    assert ok.status_code == 200, ok.content
    assert D(ok.data["lines"][0]["qty_to_pick"]) == D("3") and ok.data["notes"] == "fragile" and ok.data["status"] == "open"
    empty = api.post(f"/api/v1/pick-lists/{pl.data['id']}/lines", {"lines": [{"line_id": line["id"], "qty_to_pick": "0"}]}, format="json")
    assert empty.status_code == 422, "a load form keeps at least one line"

    sent = api.post(f"/api/v1/pick-lists/{pl.data['id']}/send", {}, format="json")
    assert sent.status_code == 200 and sent.data["status"] == "sent"
    late = api.post(f"/api/v1/pick-lists/{pl.data['id']}/lines", {"lines": [{"line_id": line["id"], "qty_to_pick": "2"}]}, format="json")
    assert late.status_code == 422 and "already gone to the warehouse" in str(late.content)
    assert _steps(api, order["id"])["load_form"]["state"] == "done"


def _issue(api, order):
    pl = api.post(f"/api/v1/sales-orders/{order['id']}/load-form", {"send": True}, format="json").data
    assert pl["status"] == "sent", "an explicit send still hands it over in one call"
    r = api.post(f"/api/v1/pick-lists/{pl['id']}/issue", {"issues": [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in pl["lines"]]}, format="json")
    assert r.status_code == 200, r.content


def test_the_create_page_saves_a_draft_to_review_and_post(api, confirmed):
    order, wh, v, cust = confirmed
    _issue(api, order)
    made = api.post(f"/api/v1/sales-orders/{order['id']}/invoice", {"post": False}, format="json")
    assert made.status_code == 201, made.content
    inv = made.data[0]
    assert inv["status"] == "draft" and inv["journal"] is None, "nothing on the books until it is posted"
    step = _steps(api, order["id"])["invoice"]
    assert step["state"] == "current" and step["action"]["label"] == "Review & post" and step["action"]["to"].endswith(inv["id"])
    posted = api.post(f"/api/v1/invoices/{inv['id']}/post", {}, format="json")
    assert posted.status_code == 200 and posted.data["status"] == "posted", posted.content
    assert _steps(api, order["id"])["invoice"]["state"] == "done"


def test_delivered_without_an_invoice_bills_a_draft_and_the_cash_waits_for_posting(api, tenant, confirmed):
    order, wh, v, cust = confirmed
    r = api.post(f"/api/v1/sales-orders/{order['id']}/mark-delivered", {"receiver_name": "Shop", "cash_collected": "200"}, format="json")
    assert r.status_code == 200, r.content
    with tenant_context(tenant, tenant.owner):
        from apps.accounting.models import Payment
        from apps.accounting.services import PaymentService
        from apps.core.exceptions import DomainError
        from apps.sales.models import SalesInvoice
        from apps.sales.services import InvoiceService

        inv = SalesInvoice.objects.get(order_id=order["id"])
        assert inv.status == "draft" and inv.paid_amount == 0, "billed as a draft for review, not posted"
        pay = Payment.objects.get(delivery_note=inv.delivery_note)
        assert pay.unallocated == D("200"), "the cash waits on the customer's account"
        with pytest.raises(DomainError):
            PaymentService.allocate(pay, [{"document_type": "sales_invoice", "document_id": str(inv.pk), "amount": D("50")}])

        InvoiceService.post(inv)
        inv.refresh_from_db()
        pay.refresh_from_db()
        assert inv.paid_amount == D("200") and pay.unallocated == 0, "posting applies the waiting cash"
        assert inv.status == "partially_paid" and inv.balance == inv.total - D("200")
