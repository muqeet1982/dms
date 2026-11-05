"""
One live load form per order. On 4 Nov 2026 TDN-PL-00132, 00155 and 00158 each came out twice: Load forms → New kept
listing an order until its stock was issued, its button stayed live while a form was being made, and nothing on the
server said no. Owner, 5 Nov: "why 2 load form … find root cause and fix the issue."
"""
from decimal import Decimal as D

import pytest

from apps.core.tenancy import tenant_context

from .conftest import seed_basic

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(api, tenant):
    wh, _sup, v, cust = seed_basic(tenant)
    return wh, v, cust


def _order(api, wh, v, cust, qty="4"):
    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True, "lines": [{"variant": str(v.id), "qty": qty}]}, format="json")
    assert r.status_code == 201, r.content
    return r.data


def _form(api, wh, *orders):
    return api.post("/api/v1/pick-lists", {"warehouse": str(wh.id), "orders": [o["id"] for o in orders]}, format="json")


def _issue(api, form):
    return api.post(f"/api/v1/pick-lists/{form['id']}/issue", {"issues": [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in form["lines"]]}, format="json")


def _new_page(api, wh):
    return [o["id"] for o in api.get("/api/v1/sales-orders", {"status": "confirmed", "warehouse": str(wh.id), "no_load_form": 1}).data["results"]]


def test_an_order_cannot_go_on_a_second_load_form_and_leaves_the_new_page(api, tenant, world):
    wh, v, cust = world
    a, b = _order(api, wh, v, cust), _order(api, wh, v, cust)
    assert sorted(_new_page(api, wh)) == sorted([a["id"], b["id"]])
    first = _form(api, wh, a, b)
    assert first.status_code == 201, first.content
    assert _new_page(api, wh) == [], "both are on a load form now, so New no longer offers them"

    again = _form(api, wh, a, b)
    assert again.status_code in (400, 422) and again.data["type"] == "duplicate", again.content
    assert a["number"] in again.data["detail"] and first.data["number"] in again.data["detail"]
    one = _form(api, wh, b)
    assert one.status_code in (400, 422) and "is already on" in one.data["detail"], "alone it is refused too (how SO-00133 got a third form)"
    button = api.post(f"/api/v1/sales-orders/{a['id']}/load-form", {}, format="json")
    assert button.status_code in (400, 422) and button.data["type"] == "duplicate"
    assert api.get("/api/v1/pick-lists").data["count"] == 1, "nothing was left behind"


def test_cancelling_a_load_form_frees_its_orders_and_closes_it(api, tenant, world):
    from apps.inventory.models import StockLevel

    wh, v, cust = world
    order = _order(api, wh, v, cust)
    form = _form(api, wh, order).data
    url = f"/api/v1/pick-lists/{form['id']}"
    assert api.post(f"{url}/send", {}, format="json").status_code == 200
    assert api.post(f"{url}/picks", {"picks": [{"line_id": form["lines"][0]["id"], "qty_picked": "2"}]}, format="json").status_code == 200

    done = api.post(f"{url}/cancel", {"reason": "made twice"}, format="json")
    assert done.status_code == 200 and done.data["status"] == "cancelled", done.content
    assert done.data["order_numbers"] == [order["number"]], "the form keeps what it was for"
    with tenant_context(tenant, tenant.owner):
        assert sum(s.qty_on_hand for s in StockLevel.objects.filter(variant=v)) == D("100"), "no stock moved"
    for action, body in (("picks", {"picks": []}), ("send", {}), ("issue", {}), ("cancel", {}), ("scans", {"scans": []})):
        assert api.post(f"{url}/{action}", body, format="json").status_code in (400, 422), f"{action} on a cancelled form"
    assert api.get(url).data["status"] == "cancelled"

    assert _new_page(api, wh) == [order["id"]], "the order is free again"
    next_form = _form(api, wh, order)
    assert next_form.status_code == 201, next_form.content
    assert _issue(api, next_form.data).status_code == 200
    assert api.get(f"/api/v1/sales-orders/{order['id']}").data["status"] == "issued"


def test_a_load_form_stock_has_left_on_cannot_be_cancelled(api, tenant, world):
    wh, v, cust = world
    form = _form(api, wh, _order(api, wh, v, cust)).data
    assert _issue(api, form).status_code == 200
    r = api.post(f"/api/v1/pick-lists/{form['id']}/cancel", {}, format="json")
    assert r.status_code in (400, 422) and r.data["type"] == "already_issued", r.content


def test_an_order_edited_off_a_shared_load_form_can_get_a_new_one(api, tenant, world):
    wh, v, cust = world
    a, b = _order(api, wh, v, cust), _order(api, wh, v, cust)
    first = _form(api, wh, a, b).data
    edited = api.patch(f"/api/v1/sales-orders/{a['id']}", {"lines": [{"variant": str(v.id), "qty": "5"}]}, format="json")
    assert edited.status_code == 200, edited.content
    assert api.get(f"/api/v1/pick-lists/{first['id']}").data["status"] != "cancelled", "the other order is still on it"
    assert _new_page(api, wh) == [a["id"]]
    assert _form(api, wh, a).status_code == 201


def test_two_batches_of_one_item_count_in_full_and_cannot_go_out_twice(api, tenant, world):
    """TDN-SO-00133: 1 + 2 issued against an order line of 3 read as dispatched 2, which let the same 3 pass the guard again."""
    from apps.core.exceptions import DomainError
    from apps.delivery.services import DeliveryService
    from apps.sales.models import SalesOrderLine

    wh, v, cust = world
    order = _order(api, wh, v, cust, qty="3")
    with tenant_context(tenant, tenant.owner):
        ol = SalesOrderLine.objects.get(order_id=order["id"])
        from apps.sales.models import SalesOrder

        so = SalesOrder.objects.get(pk=order["id"])
        DeliveryService._stock_out(DeliveryService.create_note(so, lines=[{"order_line": ol, "qty": D("1")}, {"order_line": ol, "qty": D("2")}]))
        ol.refresh_from_db()
        assert ol.qty_dispatched == D("3")
        so.refresh_from_db()
        with pytest.raises(DomainError):
            DeliveryService._stock_out(DeliveryService.create_note(so, lines=[{"order_line": ol, "qty": D("2")}, {"order_line": ol, "qty": D("1")}]))
