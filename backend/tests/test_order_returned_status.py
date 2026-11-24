"""
Owner, 24 Nov 2026: TDN-SO-00575 was returned in full on a credit note after delivery and still read Delivered. An order
whose delivered goods all came back reads Returned; deleting that return puts it back to Delivered. A partial return
leaves it Delivered.
"""
from decimal import Decimal as D

import pytest

from apps.core.tenancy import tenant_context

from .conftest import issue_order, seed_basic

pytestmark = pytest.mark.django_db


def _delivered(api, tenant, qty="3"):
    wh, _s, v, cust = seed_basic(tenant)
    o = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True, "lines": [{"variant": str(v.id), "qty": qty}]}, format="json").data
    issue_order(api, o["id"])
    inv = api.post(f"/api/v1/sales-orders/{o['id']}/invoice", {"post": True}, format="json").data[0]
    assert api.post(f"/api/v1/sales-orders/{o['id']}/mark-delivered", {}, format="json").status_code == 200
    return o, inv, v


def _return(tenant, inv_id, v, qty):
    from apps.sales.models import SalesInvoice
    from apps.sales.services import CreditNoteService

    with tenant_context(tenant, tenant.owner):
        inv = SalesInvoice.objects.get(pk=inv_id)
        cn = CreditNoteService.create(customer=inv.customer, invoice=inv, reason="customer sent it back",
                                      lines=[{"variant": v, "qty": qty, "unit_price": "150", "unit_cost": "100", "condition": "good", "restock": True}])
        return CreditNoteService.post(cn)


def test_a_full_return_after_delivery_reads_returned(api, tenant):
    o, inv, v = _delivered(api, tenant)
    assert api.get(f"/api/v1/sales-orders/{o['id']}").data["status"] == "delivered"
    _return(tenant, inv["id"], v, "1")
    assert api.get(f"/api/v1/sales-orders/{o['id']}").data["status"] == "delivered", "part of it back: still delivered"
    cn = _return(tenant, inv["id"], v, "2")
    assert api.get(f"/api/v1/sales-orders/{o['id']}").data["status"] == "returned"
    r = api.post(f"/api/v1/sales-orders/{o['id']}/cancel", {"reason": "x"}, format="json")
    assert r.status_code == 422, "a returned order is finished, not cancelled"
    from apps.sales.services import CreditNoteService

    with tenant_context(tenant, tenant.owner):
        CreditNoteService.delete_posted(cn, reason="entered by mistake", actor=tenant.owner)
    assert api.get(f"/api/v1/sales-orders/{o['id']}").data["status"] == "delivered", "the return deleted: delivered again"
