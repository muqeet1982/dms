"""
TDN-INV-00091 (owner, 26 Nov 2026: "its sale order was cancelled but invoice is still showing"): its order was cancelled on
29 Oct, before cancelling an order took its invoice with it; the invoice was reopened to a draft, and on 25 Nov the draft
was posted again — for goods that had come home in October. A draft of a cancelled order is cancelled, never posted.
"""
from decimal import Decimal as D

import pytest

from apps.core.tenancy import tenant_context

from .conftest import issue_order, seed_basic

pytestmark = pytest.mark.django_db


@pytest.fixture
def left_behind(api, tenant):
    """As TDN's: posted, reopened to a draft, its order cancelled under it (the way it happened before 29 Oct)."""
    from apps.delivery.models import DeliveryNote
    from apps.sales.models import SalesInvoice, SalesOrder
    from apps.sales.services import InvoiceService

    wh, _sup, v, cust = seed_basic(tenant)
    o = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True, "lines": [{"variant": str(v.id), "qty": "2"}]}, format="json").data
    issue_order(api, o["id"])
    inv = api.post(f"/api/v1/sales-orders/{o['id']}/invoice", {"post": True}, format="json").data[0]
    with tenant_context(tenant, tenant.owner):
        InvoiceService.reopen(SalesInvoice.objects.get(pk=inv["id"]), reason="as TDN-INV-00091 on 10 Nov")
        SalesOrder.objects.filter(pk=o["id"]).update(status="cancelled")
        DeliveryNote.objects.filter(order_id=o["id"]).update(status="cancelled")
    return inv["id"]


def test_it_cannot_be_posted(api, tenant, left_behind):
    from apps.accounting.models import JournalEntry

    r = api.post(f"/api/v1/invoices/{left_behind}/post", {}, format="json")
    assert r.status_code == 422 and r.data["type"] == "order_cancelled", r.data
    with tenant_context(tenant):
        assert not JournalEntry.objects.filter(source_id=str(left_behind), status="posted").exists(), "nothing went to the books"


def test_the_page_knows_and_it_can_be_cancelled(api, tenant, left_behind):
    from apps.accounting.models import JournalEntry

    inv = api.get(f"/api/v1/invoices/{left_behind}").data
    assert inv["status"] == "draft" and inv["order_status"] == "cancelled" and inv["delivery_status"] == "Delivery Cancelled"
    with tenant_context(tenant):
        assert JournalEntry.objects.filter(source_id=str(left_behind), status="draft").exists(), "held by the reopen"
    r = api.post(f"/api/v1/invoices/{left_behind}/cancel", {"reason": "Order cancelled"}, format="json")
    assert r.status_code == 200 and r.data["status"] == "cancelled", r.data
    with tenant_context(tenant):
        assert not JournalEntry.objects.filter(source_id=str(left_behind)).exists(), "its held entries go with it"


def test_an_invoice_of_a_live_order_still_posts(api, tenant):
    wh, _sup, v, cust = seed_basic(tenant)
    o = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True, "lines": [{"variant": str(v.id), "qty": "1"}]}, format="json").data
    issue_order(api, o["id"])
    inv = api.post(f"/api/v1/sales-orders/{o['id']}/invoice", {"post": False}, format="json").data[0]
    assert inv["status"] == "draft" and inv["order_status"] != "cancelled"
    assert api.post(f"/api/v1/invoices/{inv['id']}/post", {}, format="json").data["status"] == "posted"
