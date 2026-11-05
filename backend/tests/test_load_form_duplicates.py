"""
One load form, one order, its number. On 4 Nov 2026 TDN-PL-00132, 00155 and 00158 each came out twice: Load forms → New kept
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


def test_a_load_form_is_for_one_order_and_carries_its_number(api, tenant, world):
    """Owner, 5 Nov 2026: "why TDN-PL-00155-2 -2?? only limit one its own order that is it." """
    wh, v, cust = world
    a, b = _order(api, wh, v, cust), _order(api, wh, v, cust)
    both = _form(api, wh, a, b)
    assert both.status_code in (400, 422) and both.data["type"] == "one_order_per_load_form", both.content
    assert api.get("/api/v1/pick-lists").data["count"] == 0
    one = _form(api, wh, b)
    assert one.status_code == 201, one.content
    assert one.data["number"] == b["number"].replace("-SO-", "-PL-") and one.data["order_numbers"] == [b["number"]]


def test_an_order_cannot_get_a_second_load_form_and_leaves_the_new_page(api, tenant, world):
    wh, v, cust = world
    a, b = _order(api, wh, v, cust), _order(api, wh, v, cust)
    assert sorted(_new_page(api, wh)) == sorted([a["id"], b["id"]])
    first = _form(api, wh, a)
    assert first.status_code == 201, first.content
    assert _new_page(api, wh) == [b["id"]], "it has its load form, so New no longer offers it"

    again = _form(api, wh, a)
    assert again.status_code in (400, 422) and again.data["type"] == "duplicate", again.content
    assert a["number"] in again.data["detail"] and first.data["number"] in again.data["detail"]
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
    assert next_form.data["number"] == form["number"] and next_form.data["status"] == "open", "the same form again — no -2"
    assert next_form.data["sent_at"] is None and all(D(l["qty_picked"]) == 0 for l in next_form.data["lines"]), "and it starts clean"
    assert _issue(api, next_form.data).status_code == 200
    assert api.get(f"/api/v1/sales-orders/{order['id']}").data["status"] == "issued"


def test_a_load_form_stock_has_left_on_cannot_be_cancelled(api, tenant, world):
    wh, v, cust = world
    form = _form(api, wh, _order(api, wh, v, cust)).data
    assert _issue(api, form).status_code == 200
    r = api.post(f"/api/v1/pick-lists/{form['id']}/cancel", {}, format="json")
    assert r.status_code in (400, 422) and r.data["type"] == "already_issued", r.content


def test_an_issued_order_edited_and_issued_again_keeps_its_number_and_leaves_once(api, tenant, world):
    """How TDN-PL-00149-2 came about, 5 Nov 2026: edited after issue, the form was cancelled and the next one grew a -2."""
    from apps.inventory.models import StockLevel

    wh, v, cust = world
    order = _order(api, wh, v, cust)
    form = _form(api, wh, order).data
    assert _issue(api, form).status_code == 200
    edited = api.patch(f"/api/v1/sales-orders/{order['id']}", {"lines": [{"variant": str(v.id), "qty": "5"}]}, format="json")
    assert edited.status_code == 200, edited.content
    assert api.get(f"/api/v1/pick-lists/{form['id']}").data["status"] == "cancelled"
    again = _form(api, wh, order)
    assert again.status_code == 201 and again.data["number"] == form["number"], again.content
    assert api.get(f"/api/v1/pick-lists/{again.data['id']}").data["delivery_note_numbers"] == [], "the cancelled note is not this issue's"
    issued = _issue(api, again.data)
    assert issued.status_code == 200, issued.content
    note = issued.data["delivery_note_numbers"]
    assert [n["number"] for n in note] == [order["number"].replace("-SO-", "-DN-")], "the delivery note keeps its number too"
    with tenant_context(tenant, tenant.owner):
        assert sum(s.qty_on_hand for s in StockLevel.objects.filter(variant=v)) == D("95"), "5 out, once"
        from apps.delivery.models import DeliveryNote
        from apps.inventory.models import StockMovement

        old = DeliveryNote.objects.get(order_id=order["id"], status="cancelled")
        assert old.number == note[0]["number"] + "-C1", "the cancelled one stands aside"
        moves = StockMovement.objects.filter(reference_id=str(old.pk))
        assert moves.exists() and not moves.exclude(reference_number=old.number).exists(), "and the stock ledger says so"


def test_an_order_edited_off_a_shared_trip_gets_its_own_trip_number_back(api, tenant, world):
    """TDN-TRIP-00149 still carried SO-00133 after SO-00149 was edited off it, so SO-00149's new trip read -2."""
    from apps.delivery.models import Driver, Vehicle

    wh, v, cust = world
    a, b = _order(api, wh, v, cust), _order(api, wh, v, cust)
    with tenant_context(tenant, tenant.owner):
        driver, vehicle = Driver.objects.create(name="Akram", phone="03001234567"), Vehicle.objects.create(reg_no="LEB-765")
    def trip(*orders):
        r = api.post("/api/v1/trips", {"warehouse": str(wh.id), "driver": str(driver.id), "vehicle": str(vehicle.id), "delivery_rep": str(tenant.owner.id),
                                       "order_ids": [o["id"] for o in orders]}, format="json")
        assert r.status_code == 201, r.content
        return r.data
    shared = trip(a, b)
    named, other = (a, b) if shared["number"] == a["number"].replace("-SO-", "-TRIP-") else (b, a)
    assert shared["number"] == named["number"].replace("-SO-", "-TRIP-")
    form = _form(api, wh, named).data
    assert _issue(api, form).status_code == 200
    edited = api.patch(f"/api/v1/sales-orders/{named['id']}", {"lines": [{"variant": str(v.id), "qty": "5"}]}, format="json")
    assert edited.status_code == 200, edited.content
    again = _form(api, wh, named).data
    assert _issue(api, again).status_code == 200
    mine = trip(named)
    assert mine["number"] == named["number"].replace("-SO-", "-TRIP-"), "no -2"
    assert api.get(f"/api/v1/trips/{shared['id']}").data["number"] == other["number"].replace("-SO-", "-TRIP-"), "the old trip takes the order it carries"


def test_a_trip_carries_many_orders_though_each_has_its_own_load_form(api, tenant, world):
    """Owner, 5 Nov 2026: "in trip allow multiple sale order". One order per LOAD FORM never meant one order per TRIP."""
    from apps.delivery.models import DeliveryNote, Driver, Vehicle
    from apps.inventory.models import StockMovement

    wh, v, cust = world
    issued = [_order(api, wh, v, cust), _order(api, wh, v, cust)]
    for o in issued:
        assert _issue(api, _form(api, wh, o).data).status_code == 200
    waiting = _order(api, wh, v, cust)
    orders = [*issued, waiting]
    with tenant_context(tenant, tenant.owner):
        driver, vehicle = Driver.objects.create(name="Akram", phone="03001234567"), Vehicle.objects.create(reg_no="LEB-765")
        moves = StockMovement.objects.count()
    body = {"warehouse": str(wh.id), "driver": str(driver.id), "vehicle": str(vehicle.id), "delivery_rep": str(tenant.owner.id)}

    one_as_text = api.post("/api/v1/trips", {**body, "order_ids": waiting["id"]}, format="json")
    assert one_as_text.status_code == 400 and "order_ids" in one_as_text.data["errors"], "the error the New Trip page hit on 5 Nov"

    r = api.post("/api/v1/trips", {**body, "order_ids": [o["id"] for o in orders]}, format="json")
    assert r.status_code == 201, r.content
    assert r.data["stops_count"] == 3
    for o in orders:
        assert str(api.get(f"/api/v1/sales-orders/{o['id']}").data["trip"]) == str(r.data["id"])
    with tenant_context(tenant, tenant.owner):
        notes = [DeliveryNote.objects.filter(order_id=o["id"]).exclude(status="cancelled") for o in orders]
        assert [n.count() for n in notes] == [1, 1, 1], "the issued orders' own notes go on the vehicle, none is made twice"
        assert all(str(n.get().trip_id) == str(r.data["id"]) for n in notes)
        assert StockMovement.objects.count() == moves, "making the trip moves no stock"


def test_a_real_second_delivery_still_reads_2(api, tenant, world):
    from apps.core.numbering import derived_number
    from apps.delivery.models import DeliveryNote
    from apps.delivery.services import DeliveryService
    from apps.sales.models import SalesOrder, SalesOrderLine

    wh, v, cust = world
    order = _order(api, wh, v, cust)
    with tenant_context(tenant, tenant.owner):
        so, ol = SalesOrder.objects.get(pk=order["id"]), SalesOrderLine.objects.get(order_id=order["id"])
        first = DeliveryService.create_note(so, lines=[{"order_line": ol, "qty": D("1")}])
        second = DeliveryService.create_note(so, lines=[{"order_line": ol, "qty": D("1")}])
        assert first.number == order["number"].replace("-SO-", "-DN-") and second.number == first.number + "-2"


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
