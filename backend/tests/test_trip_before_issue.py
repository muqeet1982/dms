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


def test_a_trip_opened_before_the_issue_carries_the_issue_once(api, tenant, world):
    from apps.delivery.models import DeliveryNote
    from apps.inventory.services import StockService
    from apps.sales.models import SalesInvoice, SalesOrderLine

    wh, v, cust, driver, vehicle, o = world
    with tenant_context(tenant, tenant.owner):
        before = StockService.on_hand(v, wh)[0]
    trip = api.post("/api/v1/trips", {"warehouse": str(wh.id), "driver": str(driver.id), "vehicle": str(vehicle.id),
                                      "delivery_rep": str(tenant.owner.id), "order_ids": [o["id"]]}, format="json")
    assert trip.status_code == 201, trip.content
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
    assert api.post(f"/api/v1/trips/{trip.data['id']}/start", {}, format="json").status_code == 200
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
