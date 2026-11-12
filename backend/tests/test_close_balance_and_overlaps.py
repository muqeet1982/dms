"""
TDN-SO-00034 (owner, 12 Nov 2026): 4 pcs of NO. 6.13 were short on its 20 Oct delivery; on 21 Oct a new order, TDN-SO-00037,
sent them, but TDN-SO-00034's balance stayed open and went out again on 7 Nov. Two things now: a partly delivered order's
balance can be closed, and Confirm / Save & confirm / a balance's load form warn about the customer's other orders with
the same items.
"""
from decimal import Decimal as D

import pytest

from apps.core.tenancy import tenant_context

from .conftest import issue_order, seed_basic

pytestmark = pytest.mark.django_db


def _shade(tenant, wh):
    from apps.catalog.models import UnitOfMeasure
    from apps.catalog.services import ProductService
    from apps.inventory.services import StockService

    with tenant_context(tenant, tenant.owner):
        p = ProductService.create_product(tenant, {"sku": "62613UC", "name": "NO. 6.13 - 60 ML", "base_uom": UnitOfMeasure.objects.get(code="PC"), "cost_price": D("100"), "sale_price": D("150")})
        v = p.variants.first()
        StockService.move(warehouse=wh, variant=v, qty=D("48"), movement_type="opening", unit_cost=D("100"))
        return v


def _order(api, wh, cust, lines):
    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True, "lines": [{"variant": str(v.id), "qty": q} for v, q in lines]}, format="json")
    assert r.status_code == 201 and r.data["status"] == "confirmed", r.content
    return r.data


def _issue(api, order_id, short=None):
    """Issue the order's load form, with `short` (a variant) issued as 0 — the warehouse sent none of it."""
    pl = api.post(f"/api/v1/sales-orders/{order_id}/load-form", {"send": True}, format="json")
    assert pl.status_code == 201, pl.content
    issues = [{"line_id": l["id"], "qty_issued": "0" if short is not None and str(l["variant"]) == str(short.id) else l["qty_to_pick"]} for l in pl.data["lines"]]
    assert api.post(f"/api/v1/pick-lists/{pl.data['id']}/issue", {"issues": issues}, format="json").status_code == 200
    return pl.data


def _deliver(api, tenant, order_id):
    from apps.delivery.models import DeliveryNote

    with tenant_context(tenant):
        dn = DeliveryNote.objects.filter(order_id=order_id, status__in=("pending", "loaded", "dispatched")).latest("created_at")
    assert api.post(f"/api/v1/delivery-notes/{dn.pk}/deliver", {"receiver_name": "Shop"}, format="json").status_code == 200


def _partly_delivered(api, tenant, wh, v, shade, cust):
    a = _order(api, wh, cust, [(v, "5"), (shade, "4")])
    _issue(api, a["id"], short=shade)
    _deliver(api, tenant, a["id"])
    assert api.get(f"/api/v1/sales-orders/{a['id']}").data["status"] == "partially_delivered"
    return a


def test_the_tdn_so_00034_case_is_warned_twice_and_the_balance_closes(api, tenant):
    from apps.inventory.services import StockService

    wh, _s, v, cust = seed_basic(tenant)
    shade = _shade(tenant, wh)
    a = _partly_delivered(api, tenant, wh, v, shade, cust)            # TDN-SO-00034: 4 pcs of the shade still owed

    # a new order for the same shade (TDN-SO-00037) is warned before Save & confirm: A still owes it
    w = api.get("/api/v1/sales-orders/overlaps", {"customer": str(cust.id), "variants": str(shade.id)}).data
    assert [(x["number"], x["items"][0]["left"]) for x in w] == [(a["number"], "4")], w
    b = _order(api, wh, cust, [(shade, "4")])
    assert [x["number"] for x in api.get("/api/v1/sales-orders/overlaps", {"order": b["id"]}).data] == [a["number"]], "and before Confirm"
    issue_order(api, b["id"])
    _deliver(api, tenant, b["id"])

    # only orders whose item has not left the warehouse are named (owner, 12 Nov 2026): B, delivered, is not
    assert api.get("/api/v1/sales-orders/overlaps", {"order": a["id"]}).data == []

    with tenant_context(tenant):
        stock_before = StockService.available(shade, wh)
    r = api.post(f"/api/v1/sales-orders/{a['id']}/close-balance", {"reason": "sent on " + b["number"]}, format="json")
    assert r.status_code == 200 and r.data["status"] == "delivered", r.content
    line = next(l for l in r.data["lines"] if str(l["variant"]) == str(shade.id))
    assert D(line["qty_closed"]) == 4 and D(line["qty_pending"]) == 0
    with tenant_context(tenant):
        assert StockService.available(shade, wh) == stock_before, "no stock moves: the 4 never left"
    r = api.post(f"/api/v1/sales-orders/{a['id']}/load-form", {"send": True}, format="json")
    assert r.status_code == 422, "nothing left to send"
    assert api.get("/api/v1/sales-orders/overlaps", {"customer": str(cust.id), "variants": str(shade.id)}).data == [], "nothing owed any more"


def test_closing_takes_back_an_unissued_balance_form_but_not_goods_on_the_way(api, tenant):
    from apps.delivery.models import PickList

    wh, _s, v, cust = seed_basic(tenant)
    shade = _shade(tenant, wh)
    a = _partly_delivered(api, tenant, wh, v, shade, cust)
    pl = api.post(f"/api/v1/sales-orders/{a['id']}/load-form", {"send": True}, format="json")
    assert pl.status_code == 201, pl.content
    assert api.post(f"/api/v1/sales-orders/{a['id']}/close-balance", {"reason": "x"}, format="json").status_code == 200
    with tenant_context(tenant):
        assert PickList.objects.get(pk=pl.data["id"]).status == "cancelled"

    c = _partly_delivered(api, tenant, wh, v, shade, cust)
    _issue(api, c["id"])                                              # the balance is issued: on its way
    r = api.post(f"/api/v1/sales-orders/{c['id']}/close-balance", {"reason": "x"}, format="json")
    assert r.status_code == 422 and r.data["type"] == "balance_on_the_road", r.content


def test_only_a_partly_delivered_order_has_a_balance_and_only_the_owner_closes_it(api, tenant):
    from unittest import mock

    from apps.accounts.models import TenantMembership

    wh, _s, v, cust = seed_basic(tenant)
    o = _order(api, wh, cust, [(v, "2")])
    r = api.post(f"/api/v1/sales-orders/{o['id']}/close-balance", {}, format="json")
    assert r.status_code == 422 and r.data["type"] == "no_balance"

    shade = _shade(tenant, wh)
    a = _partly_delivered(api, tenant, wh, v, shade, cust)
    real_owner = TenantMembership.is_owner
    with mock.patch.object(TenantMembership, "is_owner", new_callable=mock.PropertyMock, return_value=False), \
            mock.patch.object(TenantMembership, "has_perm", lambda self, c: c != "sales.cancel_confirmed_order"):
        r = api.post(f"/api/v1/sales-orders/{a['id']}/close-balance", {}, format="json")
    assert r.status_code == 403, r.content
    assert real_owner is TenantMembership.is_owner


def test_a_repeat_order_after_delivery_is_not_a_warning(api, tenant):
    """Salons order the same shades every week: an earlier order delivered in full is not a duplicate."""
    wh, _s, v, cust = seed_basic(tenant)
    o = _order(api, wh, cust, [(v, "2")])
    issue_order(api, o["id"])
    _deliver(api, tenant, o["id"])
    assert api.get("/api/v1/sales-orders/overlaps", {"customer": str(cust.id), "variants": str(v.id)}).data == []


def test_an_order_already_issued_is_not_named(api, tenant):
    """Owner, 12 Nov 2026: "should only show submitted not issued from warehouse"."""
    wh, _s, v, cust = seed_basic(tenant)
    issued = _order(api, wh, cust, [(v, "2")])
    issue_order(api, issued["id"])                                     # out of the warehouse, not yet delivered
    waiting = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "lines": [{"variant": str(v.id), "qty": "3"}]}, format="json").data
    w = api.get("/api/v1/sales-orders/overlaps", {"customer": str(cust.id), "variants": str(v.id)}).data
    assert [x["number"] for x in w] == [waiting["number"]], "the submitted one only"
    assert w[0]["items"][0]["left"] == "3"
