"""
27 Oct 2026, from TDN-SO-00044: a trip opened BEFORE the warehouse issued the load form made its own delivery note,
the issue made a second one, and the order left the warehouse twice and was billed twice. Plus the day's smaller
asks: a trip names its delivery person; invoice downloads carry number + customer; invoices filter by customer type.
"""
from decimal import Decimal
from urllib.parse import unquote

import pytest

from apps.core.tenancy import tenant_context

pytestmark = pytest.mark.django_db
D = Decimal


@pytest.fixture
def world(api, tenant):
    from apps.delivery.models import Driver, Vehicle

    from .conftest import seed_basic

    wh, _sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        driver = Driver.objects.create(name="Akram", phone="03001234567")
        vehicle = Vehicle.objects.create(reg_no="LEB-765")
    o = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True,
                                          "lines": [{"variant": str(v.id), "qty": "5"}]}, format="json").data
    return wh, v, cust, driver, vehicle, o


def _stop_from_before_the_rule(tenant, world):
    """A trip made before 5 Nov 2026 could hold an order the warehouse had not issued. The app can no longer make one."""
    from apps.delivery.models import DeliveryTrip
    from apps.delivery.services import DeliveryService
    from apps.sales.models import SalesOrder

    wh, v, cust, driver, vehicle, o = world
    with tenant_context(tenant, tenant.owner):
        order = SalesOrder.objects.get(pk=o["id"])
        trip = DeliveryTrip.objects.create(number="OLD-TRIP-1", warehouse=wh, driver=driver, vehicle=vehicle, delivery_rep=tenant.owner)
        DeliveryService.create_note(order, trip=trip, sequence=1)
        order.trip = trip
        order.save(update_fields=["trip"])
    return trip


def test_a_trip_opened_before_the_issue_carries_the_issue_once(api, tenant, world):
    from apps.delivery.models import DeliveryNote
    from apps.inventory.services import StockService
    from apps.sales.models import SalesInvoice, SalesOrderLine

    wh, v, cust, driver, vehicle, o = world
    with tenant_context(tenant, tenant.owner):
        before = StockService.on_hand(v, wh)[0]
    trip = _stop_from_before_the_rule(tenant, world)
    refused = api.post(f"/api/v1/trips/{trip.id}/start", {}, format="json")
    assert refused.status_code == 422 and refused.data["type"] == "not_issued", refused.content
    with tenant_context(tenant, tenant.owner):
        assert StockService.on_hand(v, wh)[0] == before, "starting took nothing off the shelf"
    pl = api.post(f"/api/v1/sales-orders/{o['id']}/load-form", {"send": True}, format="json").data
    issued = api.post(f"/api/v1/pick-lists/{pl['id']}/issue", {"issues": [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in pl["lines"]]}, format="json")
    assert issued.status_code == 200, issued.content
    with tenant_context(tenant, tenant.owner):
        notes = list(DeliveryNote.objects.filter(order_id=o["id"]))
        assert len(notes) == 1, "the trip's note carries the issue — no second note"
        assert notes[0].trip_id and notes[0].picklist_id and notes[0].stock_out_done
        assert StockService.on_hand(v, wh)[0] == before - 5

    inv = api.post(f"/api/v1/sales-orders/{o['id']}/invoice", {}, format="json")
    assert inv.status_code == 201, inv.content
    assert api.post(f"/api/v1/trips/{trip.id}/start", {}, format="json").status_code == 200
    note = notes[0]
    assert api.post(f"/api/v1/delivery-notes/{note.pk}/deliver", {"receiver_name": "Shop"}, format="json").status_code == 200
    with tenant_context(tenant, tenant.owner):
        assert StockService.on_hand(v, wh)[0] == before - 5, "out of the warehouse ONCE"
        assert SalesInvoice.objects.filter(order_id=o["id"]).exclude(status="cancelled").count() == 1, "billed ONCE"
        assert SalesOrderLine.objects.get(order_id=o["id"]).qty_dispatched == 5


def test_an_order_line_never_leaves_the_warehouse_twice(api, tenant, world):
    from apps.core.exceptions import DomainError
    from apps.delivery.services import DeliveryService
    from apps.sales.models import SalesOrder

    wh, v, cust, driver, vehicle, o = world
    pl = api.post(f"/api/v1/sales-orders/{o['id']}/load-form", {"send": True}, format="json").data
    api.post(f"/api/v1/pick-lists/{pl['id']}/issue", {"issues": [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in pl["lines"]]}, format="json")
    with tenant_context(tenant, tenant.owner):
        order = SalesOrder.objects.get(pk=o["id"])
        line = order.lines.get()
        from apps.delivery.models import DeliveryNote, DeliveryNoteLine

        dn = DeliveryNote.objects.create(number="DUP-1", order=order, customer=order.customer, warehouse=wh)
        DeliveryNoteLine.objects.create(delivery_note=dn, order_line=line, variant=v, qty_loaded=D("5"), unit_cost=D("100"))
        with pytest.raises(DomainError) as e:
            DeliveryService._stock_out(dn)
        assert "issue it twice" in str(e.value.detail)


def test_a_trip_needs_its_delivery_person(api, tenant, world):
    wh, v, cust, driver, vehicle, o = world
    r = api.post("/api/v1/trips", {"warehouse": str(wh.id), "driver": str(driver.id), "order_ids": [o["id"]]}, format="json")
    assert r.status_code in (400, 422) and "delivery person" in str(r.content).lower()
    pl = api.post(f"/api/v1/sales-orders/{o['id']}/load-form", {"send": True}, format="json").data
    api.post(f"/api/v1/pick-lists/{pl['id']}/issue", {"issues": [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in pl["lines"]]}, format="json")
    d = api.post(f"/api/v1/pick-lists/{pl['id']}/create-dispatch", {"driver": str(driver.id)}, format="json")
    assert d.status_code in (400, 422) and "delivery person" in str(d.content).lower()
    ok = api.post(f"/api/v1/pick-lists/{pl['id']}/create-dispatch", {"driver": str(driver.id), "delivery_rep": str(tenant.owner.id)}, format="json")
    assert ok.status_code == 201, ok.content


def test_an_invoice_downloads_as_number_and_customer_and_lists_filter_by_customer_type(api, tenant, world):
    from apps.partners.models import Customer

    wh, v, cust, driver, vehicle, o = world
    pl = api.post(f"/api/v1/sales-orders/{o['id']}/load-form", {"send": True}, format="json").data
    api.post(f"/api/v1/pick-lists/{pl['id']}/issue", {"issues": [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in pl["lines"]]}, format="json")
    inv = api.post(f"/api/v1/sales-orders/{o['id']}/invoice", {}, format="json").data[0]
    r = api.get(f"/api/v1/invoices/{inv['id']}/pdf")
    cd = r["Content-Disposition"]
    assert r.status_code == 200 and f'filename="{inv["number"]} - {cust.name}.pdf"' in cd, cd
    assert unquote(cd.split("filename*=UTF-8''", 1)[1]) == f"{inv['number']} - {cust.name}.pdf"

    with tenant_context(tenant, tenant.owner):
        Customer.objects.filter(pk=cust.pk).update(type="salon")
    salons = api.get("/api/v1/invoices", {"customer_type": "salon"}).data["results"]
    assert [i["number"] for i in salons] == [inv["number"]]
    assert api.get("/api/v1/invoices", {"customer_type": "retailer,wholesaler"}).data["results"] == []


def _on_trip_and_issued(api, tenant, world):
    wh, v, cust, driver, vehicle, o = world
    trip = _stop_from_before_the_rule(tenant, world)
    pl = api.post(f"/api/v1/sales-orders/{o['id']}/load-form", {"send": True}, format="json").data
    issued = api.post(f"/api/v1/pick-lists/{pl['id']}/issue", {"issues": [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in pl["lines"]]}, format="json")
    assert issued.status_code == 200, issued.content
    return {"id": str(trip.id)}


def _offered(api, **query):
    return [r["number"] for r in api.get("/api/v1/sales-orders", {"uninvoiced": 1, **query}).data["results"]]


def test_new_invoice_finds_an_order_issued_while_on_a_trip(api, tenant, world):
    """TDN-SO-00133, 5 Nov 2026: on a trip before the warehouse issued, it went Confirmed → Dispatched, never read Issued,
    and the New Invoice search only looked at Issued orders. Nobody could bill it."""
    wh, v, cust, driver, vehicle, o = world
    assert _offered(api) == [], "nothing has left the warehouse yet"
    _on_trip_and_issued(api, tenant, world)
    assert api.get(f"/api/v1/sales-orders/{o['id']}").data["status"] == "dispatched"
    assert _offered(api) == [o["number"]] and _offered(api, search=o["number"]) == [o["number"]]

    inv = api.post(f"/api/v1/sales-orders/{o['id']}/invoice", {"post": False}, format="json")
    assert inv.status_code == 201, inv.content
    assert _offered(api) == [], "billed: it leaves the search, so it cannot be billed twice"
    assert api.post(f"/api/v1/sales-orders/{o['id']}/invoice", {}, format="json").status_code in (400, 422)


def test_new_invoice_still_finds_an_order_issued_with_no_trip_and_one_already_out(api, tenant, world):
    wh, v, cust, driver, vehicle, o = world
    pl = api.post(f"/api/v1/sales-orders/{o['id']}/load-form", {"send": True}, format="json").data
    api.post(f"/api/v1/pick-lists/{pl['id']}/issue", {"issues": [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in pl["lines"]]}, format="json")
    assert api.get(f"/api/v1/sales-orders/{o['id']}").data["status"] == "issued" and _offered(api) == [o["number"]]
    trip = api.post("/api/v1/trips", {"warehouse": str(wh.id), "driver": str(driver.id), "vehicle": str(vehicle.id),
                                      "delivery_rep": str(tenant.owner.id), "order_ids": [o["id"]]}, format="json").data
    assert api.post(f"/api/v1/trips/{trip['id']}/start", {}, format="json").status_code == 200
    assert _offered(api) == [o["number"]], "out on the vehicle, still unbilled"


def test_a_failed_delivery_is_neither_offered_nor_billed(api, tenant, world):
    """Its goods did not stay with the customer: it goes out again on its own invoice, or the order closes."""
    from apps.delivery.models import DeliveryNote

    wh, v, cust, driver, vehicle, o = world
    trip = _on_trip_and_issued(api, tenant, world)
    assert api.post(f"/api/v1/trips/{trip['id']}/start", {}, format="json").status_code == 200
    with tenant_context(tenant, tenant.owner):
        note = DeliveryNote.objects.get(order_id=o["id"])
    failed = api.post(f"/api/v1/delivery-notes/{note.pk}/deliver", {"failed": True, "failure_reason": "Shop closed"}, format="json")
    assert failed.status_code == 200, failed.content
    assert _offered(api) == []
    assert api.post(f"/api/v1/sales-orders/{o['id']}/invoice", {}, format="json").status_code in (400, 422)


def test_no_load_form_no_issue_no_trip(api, tenant, world):
    """Owner, 5 Nov 2026, on DD-SO-00119: "there is no load form and stock not issued why then how it has dispatched?"
    Every way onto a trip takes only what the warehouse has issued, and the lists offer only that."""
    from apps.delivery.models import DeliveryNote, DeliveryTrip, PickList
    from apps.delivery.services import PickListService
    from apps.inventory.models import StockMovement

    wh, v, cust, driver, vehicle, o = world
    crew = {"warehouse": str(wh.id), "driver": str(driver.id), "vehicle": str(vehicle.id), "delivery_rep": str(tenant.owner.id)}
    offered = lambda: [r["id"] for r in api.get("/api/v1/sales-orders", {"ready_for_trip": 1, "warehouse": str(wh.id)}).data["results"]]

    def refused(r):
        assert r.status_code == 422 and r.data["type"] == "not_issued", r.content

    with tenant_context(tenant, tenant.owner):
        moves = StockMovement.objects.count()
    assert offered() == [], "confirmed, no load form"
    refused(api.post("/api/v1/trips", {**crew, "order_ids": [o["id"]]}, format="json"))
    with tenant_context(tenant, tenant.owner):
        assert not DeliveryTrip.objects.exists(), "the refused trip left nothing behind"

    pl = api.post(f"/api/v1/sales-orders/{o['id']}/load-form", {"send": True}, format="json").data
    assert offered() == [], "a load form, nothing issued yet"
    refused(api.post("/api/v1/trips", {**crew, "order_ids": [o["id"]]}, format="json"))
    empty = api.post("/api/v1/trips", {**crew, "order_ids": []}, format="json").data
    refused(api.post(f"/api/v1/trips/{empty['id']}/assign-orders", {"order_ids": [o["id"]]}, format="json"))

    with tenant_context(tenant, tenant.owner):
        PickListService.complete(PickList.objects.get(pk=pl["id"]))   # packed is not issued
    packed = api.post(f"/api/v1/pick-lists/{pl['id']}/create-dispatch", crew, format="json")
    assert packed.status_code == 422 and "issue the load form" in packed.data["detail"], packed.content
    with tenant_context(tenant, tenant.owner):
        assert not DeliveryNote.objects.filter(order_id=o["id"]).exists() and StockMovement.objects.count() == moves

    issued = api.post(f"/api/v1/pick-lists/{pl['id']}/issue", {"issues": [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in pl["lines"]]}, format="json")
    assert issued.status_code == 200, issued.content
    assert offered() == [o["id"]], "issued: now it is offered"
    added = api.post(f"/api/v1/trips/{empty['id']}/assign-orders", {"order_ids": [o["id"]]}, format="json")
    assert added.status_code == 200 and added.data["stops_count"] == 1, added.content
    assert offered() == [], "on a vehicle: no longer offered"


def test_mark_delivered_only_delivers_what_the_warehouse_issued(api, tenant, world):
    """Owner, 5 Nov 2026. It used to make its own note and take the stock with no load form."""
    from apps.delivery.models import DeliveryNote
    from apps.inventory.models import StockMovement
    from apps.inventory.services import StockService

    wh, v, cust, driver, vehicle, o = world
    with tenant_context(tenant, tenant.owner):
        before, moves = StockService.on_hand(v, wh)[0], StockMovement.objects.count()
    mark = lambda: api.post(f"/api/v1/sales-orders/{o['id']}/mark-delivered", {"receiver_name": "Shop"}, format="json")

    r = mark()
    assert r.status_code == 422 and r.data["type"] == "not_issued", r.content
    pl = api.post(f"/api/v1/sales-orders/{o['id']}/load-form", {"send": True}, format="json").data
    r = mark()
    assert r.status_code == 422 and r.data["type"] == "not_issued", "a form that is not issued changes nothing"
    with tenant_context(tenant, tenant.owner):
        assert not DeliveryNote.objects.filter(order_id=o["id"]).exists()
        assert StockService.on_hand(v, wh)[0] == before and StockMovement.objects.count() == moves

    api.post(f"/api/v1/pick-lists/{pl['id']}/issue", {"issues": [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in pl["lines"]]}, format="json")
    assert api.get(f"/api/v1/sales-orders/{o['id']}").data["status"] == "issued"
    r = mark()
    assert r.status_code == 200, r.content
    with tenant_context(tenant, tenant.owner):
        notes = list(DeliveryNote.objects.filter(order_id=o["id"]))
        assert len(notes) == 1 and notes[0].status == "delivered" and notes[0].picklist_id, "the issued note, not a new one"
        assert StockService.on_hand(v, wh)[0] == before - 5, "out of the warehouse once"
    r = mark()
    assert r.status_code == 422, "nothing left to deliver"


def test_mark_delivered_delivers_the_stop_on_a_trip(api, tenant, world):
    """It used to answer "nothing left to deliver" on a Dispatched order, or make a second note for any balance."""
    from apps.delivery.models import DeliveryNote
    from apps.sales.models import SalesInvoice

    wh, v, cust, driver, vehicle, o = world
    pl = api.post(f"/api/v1/sales-orders/{o['id']}/load-form", {"send": True}, format="json").data
    api.post(f"/api/v1/pick-lists/{pl['id']}/issue", {"issues": [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in pl["lines"]]}, format="json")
    trip = api.post("/api/v1/trips", {"warehouse": str(wh.id), "driver": str(driver.id), "vehicle": str(vehicle.id),
                                      "delivery_rep": str(tenant.owner.id), "order_ids": [o["id"]]}, format="json").data
    assert api.post(f"/api/v1/trips/{trip['id']}/start", {}, format="json").status_code == 200
    r = api.post(f"/api/v1/sales-orders/{o['id']}/mark-delivered", {"receiver_name": "Shop"}, format="json")
    assert r.status_code == 200 and r.data["status"] != "partially_delivered", r.content
    with tenant_context(tenant, tenant.owner):
        note = DeliveryNote.objects.get(order_id=o["id"])
        assert note.status == "delivered" and str(note.trip_id) == trip["id"]
        assert SalesInvoice.objects.filter(order_id=o["id"]).count() == 1


def test_a_balance_goes_out_on_a_load_form_of_its_own(api, tenant, world):
    """TDN-SO-00034 was issued short and TDN-SO-00149 had a unit come back. Mark Delivered was their only way out, with no load form."""
    from apps.inventory.services import StockService

    wh, v, cust, driver, vehicle, o = world
    with tenant_context(tenant, tenant.owner):
        before = StockService.on_hand(v, wh)[0]
    offered = lambda: [r["id"] for r in api.get("/api/v1/sales-orders", {"status__in": "confirmed,partially_delivered", "no_load_form": 1, "warehouse": str(wh.id)}).data["results"]]
    mark = lambda: api.post(f"/api/v1/sales-orders/{o['id']}/mark-delivered", {"receiver_name": "Shop"}, format="json")
    assert offered() == [o["id"]]

    first = api.post("/api/v1/pick-lists", {"warehouse": str(wh.id), "orders": [o["id"]]}, format="json").data
    assert api.post(f"/api/v1/pick-lists/{first['id']}/issue", {"issues": [{"line_id": first["lines"][0]["id"], "qty_issued": "3"}]}, format="json").status_code == 200
    assert offered() == [], "issued, not delivered yet: not partly delivered, so nothing to offer"
    assert mark().status_code == 200
    assert api.get(f"/api/v1/sales-orders/{o['id']}").data["status"] == "partially_delivered"
    assert offered() == [o["id"]], "3 of 5 delivered: the balance needs a form"

    r = mark()
    assert r.status_code == 422 and r.data["type"] == "not_issued", "the balance has not been issued"
    with tenant_context(tenant, tenant.owner):
        assert StockService.on_hand(v, wh)[0] == before - 3

    second = api.post("/api/v1/pick-lists", {"warehouse": str(wh.id), "orders": [o["id"]]}, format="json")
    assert second.status_code == 201, second.content
    assert second.data["number"] == first["number"] + "-2" and sum(D(l["qty_to_pick"]) for l in second.data["lines"]) == 2
    assert offered() == [], "its form is open"
    again = api.post("/api/v1/pick-lists", {"warehouse": str(wh.id), "orders": [o["id"]]}, format="json")
    assert again.status_code == 422 and again.data["type"] == "duplicate", "one live form at a time still holds"

    assert api.post(f"/api/v1/pick-lists/{second.data['id']}/issue", {"issues": [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in second.data["lines"]]}, format="json").status_code == 200
    assert mark().status_code == 200
    final = api.get(f"/api/v1/sales-orders/{o['id']}").data
    assert final["status"] != "partially_delivered", final["status"]
    assert offered() == []
    with tenant_context(tenant, tenant.owner):
        assert StockService.on_hand(v, wh)[0] == before - 5, "5 in all, nothing twice"
    assert api.post("/api/v1/pick-lists", {"warehouse": str(wh.id), "orders": [o["id"]]}, format="json").status_code == 422


def test_a_started_trip_shows_the_value_it_carries(api, tenant, world):
    """Start only counted stock it took itself, so 36 of TDN's 37 started trips read a loaded value of 0."""
    from apps.delivery.models import DeliveryNoteLine

    wh, v, cust, driver, vehicle, o = world
    pl = api.post(f"/api/v1/sales-orders/{o['id']}/load-form", {"send": True}, format="json").data
    api.post(f"/api/v1/pick-lists/{pl['id']}/issue", {"issues": [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in pl["lines"]]}, format="json")
    trip = api.post("/api/v1/trips", {"warehouse": str(wh.id), "driver": str(driver.id), "vehicle": str(vehicle.id),
                                      "delivery_rep": str(tenant.owner.id), "order_ids": [o["id"]]}, format="json").data
    started = api.post(f"/api/v1/trips/{trip['id']}/start", {}, format="json")
    assert started.status_code == 200, started.content
    with tenant_context(tenant, tenant.owner):
        cost = sum(l.qty_loaded * l.unit_cost for l in DeliveryNoteLine.objects.filter(delivery_note__order_id=o["id"]))
    assert cost > 0 and D(started.data["loaded_value"]) == cost
