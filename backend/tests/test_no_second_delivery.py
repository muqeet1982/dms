"""
Owner, 12 Nov 2026: "if an order has 4 items and they are delivered, will the system allow to deliver another 4?" No — not
on the same order, by any door: no new load form, no Mark delivered, no second delivery of its note, no second invoice,
not even after the customer returned them. A new order for 4 more is ordinary repeat business, and it is allowed.
"""
from decimal import Decimal as D

import pytest

from apps.core.tenancy import tenant_context

from .conftest import issue_order, seed_basic

pytestmark = pytest.mark.django_db


def test_a_delivered_order_never_goes_out_again(api, tenant):
    from apps.delivery.models import DeliveryNote
    from apps.inventory.services import StockService

    wh, _s, v, cust = seed_basic(tenant)
    o = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True, "lines": [{"variant": str(v.id), "qty": "4"}]}, format="json").data
    issue_order(api, o["id"])
    inv = api.post(f"/api/v1/sales-orders/{o['id']}/invoice", {"post": True}, format="json").data[0]
    with tenant_context(tenant):
        dn = DeliveryNote.objects.get(order_id=o["id"])
    assert api.post(f"/api/v1/delivery-notes/{dn.pk}/deliver", {"receiver_name": "Shop"}, format="json").status_code == 200
    assert api.get(f"/api/v1/sales-orders/{o['id']}").data["status"] == "delivered"
    with tenant_context(tenant):
        stock = StockService.available(v, wh)

    def refused(r, what):
        assert r.status_code in (400, 403, 409, 422), f"{what}: {r.status_code} {r.content[:200]}"

    refused(api.post(f"/api/v1/sales-orders/{o['id']}/load-form", {"send": True}, format="json"), "a load form from the order")
    refused(api.post("/api/v1/pick-lists", {"warehouse": str(wh.pk), "orders": [o["id"]]}, format="json"), "Load forms → New")
    refused(api.post(f"/api/v1/sales-orders/{o['id']}/mark-delivered", {"receiver_name": "Shop"}, format="json"), "Mark delivered")
    refused(api.post(f"/api/v1/delivery-notes/{dn.pk}/deliver", {"receiver_name": "Shop"}, format="json"), "its note delivered twice")
    refused(api.post(f"/api/v1/sales-orders/{o['id']}/invoice", {"post": True}, format="json"), "a second invoice")
    with tenant_context(tenant):
        assert StockService.available(v, wh) == stock, "nothing more left the shelf"
        assert DeliveryNote.objects.filter(order_id=o["id"]).exclude(status="cancelled").count() == 1

    # the customer sends all 4 back: still no second delivery on this order
    r = api.post("/api/v1/credit-notes", {"customer": str(cust.id), "invoice": inv["id"], "warehouse": str(wh.pk), "reason": "returned",
                                          "lines": [{"variant": str(v.id), "qty": "4", "restock": True, "condition": "good"}]}, format="json")
    assert r.status_code == 201, r.content
    assert api.post(f"/api/v1/credit-notes/{r.data['id']}/post", {}, format="json").status_code == 200
    refused(api.post(f"/api/v1/sales-orders/{o['id']}/load-form", {"send": True}, format="json"), "a load form after a return")

    # 4 more is a new order
    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True, "lines": [{"variant": str(v.id), "qty": "4"}]}, format="json")
    assert r.status_code == 201 and r.data["status"] == "confirmed", r.content
    assert api.get("/api/v1/sales-orders/overlaps", {"order": r.data["id"]}).data == [], "a repeat of a delivered order is no warning"
    assert D(r.data["lines"][0]["qty_ordered"]) == 4
