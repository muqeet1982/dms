"""
The order-to-delivery chain in the sequence the business runs it:
sales order → load form → warehouse issue (stock drops) → invoice → dispatch (vehicle, driver, helper)
→ out for delivery → delivery confirmation (received by, delivered/returned, remarks).
"""
import pytest
from decimal import Decimal

pytestmark = pytest.mark.django_db


@pytest.fixture
def order_with_stock(api, tenant):
    """A confirmed order for 5 units, stock on hand in the main warehouse."""
    from apps.core.tenancy import tenant_context

    from .conftest import seed_basic

    wh, _sup, variant, customer = seed_basic(tenant)
    r = api.post("/api/v1/sales-orders", {"customer": str(customer.id), "warehouse": str(wh.id), "confirm": True, "lines": [{"variant": str(variant.id), "qty": "5"}]}, format="json")
    assert r.status_code == 201 and r.data["status"] == "confirmed", r.content
    with tenant_context(tenant, tenant.owner):
        from apps.sales.models import SalesOrder

        order = SalesOrder.objects.get(pk=r.data["id"])
    return order, wh, variant


def _steps(api, order_id):
    r = api.get(f"/api/v1/sales-orders/{order_id}/workflow")
    assert r.status_code == 200, r.content
    return {s["key"]: s for s in r.data["steps"]}


def test_full_chain(api, tenant, order_with_stock):
    order, warehouse, variant = order_with_stock
    from apps.core.tenancy import tenant_context
    from apps.inventory.services import StockService

    def on_hand():
        with tenant_context(tenant, tenant.owner):
            return StockService.on_hand(variant, warehouse)[0]

    on_hand_before = on_hand()

    steps = _steps(api, order.pk)
    assert [s["key"] for s in steps.values()] == ["order", "load_form", "issue", "invoice", "dispatch", "out_for_delivery", "delivered"]
    assert steps["order"]["state"] == "done" and steps["load_form"]["state"] == "current"
    assert steps["load_form"]["action"]["post"].endswith("/load-form")

    # 2 · load form, straight to the warehouse
    r = api.post(f"/api/v1/sales-orders/{order.pk}/load-form", {}, format="json")
    assert r.status_code == 201, r.content
    pl = r.data
    assert pl["status"] == "sent" and pl["order_numbers"] == [order.number] and pl["sent_at"]
    assert api.post(f"/api/v1/sales-orders/{order.pk}/load-form", {}, format="json").status_code == 422  # only one open form

    steps = _steps(api, order.pk)
    assert steps["load_form"]["state"] == "done" and steps["issue"]["state"] == "current"

    # 3 · warehouse issue — one unit short of the request, stock drops by what was issued
    lines = [{"line_id": l["id"], "qty_issued": str(Decimal(l["qty_to_pick"]) - 1)} for l in pl["lines"]]
    r = api.post(f"/api/v1/pick-lists/{pl['id']}/issue", {"issues": lines}, format="json")
    assert r.status_code == 200, r.content
    assert r.data["status"] == "partially_issued" and r.data["issued_at"] and r.data["delivery_note_numbers"]
    issued_qty = sum(Decimal(l["qty_issued"]) for l in lines)
    assert on_hand() == on_hand_before - issued_qty
    order.refresh_from_db()
    assert order.status == "issued"

    # 4 · invoice for what was issued
    r = api.post(f"/api/v1/sales-orders/{order.pk}/invoice", {}, format="json")
    assert r.status_code == 201, r.content
    invoice = r.data[0]
    assert Decimal(invoice["total"]) > 0 and invoice["status"] in ("posted", "partially_paid")
    steps = _steps(api, order.pk)
    assert steps["issue"]["state"] == "done" and steps["invoice"]["state"] == "done"
    assert steps["dispatch"]["state"] == "current"

    # 5 · dispatch: vehicle + driver + helper, reusing the note the warehouse issued
    from apps.accounts.models import User
    from apps.delivery.models import DeliveryNote, Driver, Vehicle

    with tenant_context(tenant, tenant.owner):
        vehicle = Vehicle.objects.create(reg_no="LEA-1234", type="van")
        driver = Driver.objects.create(name="Akram", phone="03001234567")
    helper = User.objects.filter(memberships__tenant=tenant).first()
    r = api.post("/api/v1/trips", {"warehouse": str(warehouse.pk), "driver": str(driver.pk), "vehicle": str(vehicle.pk), "helper": str(helper.pk), "order_ids": [str(order.pk)]}, format="json")
    assert r.status_code == 201, r.content
    trip = r.data
    assert trip["helper_name"] and trip["driver_name"] == "Akram" and trip["vehicle_reg"] == "LEA-1234"
    with tenant_context(tenant, tenant.owner):
        assert DeliveryNote.objects.filter(order=order).count() == 1, "the issued note goes on the vehicle, no second issue"

    # 6 · out for delivery
    r = api.post(f"/api/v1/trips/{trip['id']}/start", {}, format="json")
    assert r.status_code == 200, r.content
    assert r.data["status"] == "out_for_delivery"
    order.refresh_from_db()
    assert order.status == "dispatched"
    steps = _steps(api, order.pk)
    assert steps["out_for_delivery"]["state"] == "done" and steps["delivered"]["state"] == "current"

    # 7 · delivery confirmation with a short return and remarks
    with tenant_context(tenant, tenant.owner):
        note = DeliveryNote.objects.get(order=order)
        dn_lines = list(note.lines.all())
    payload = {
        "receiver_name": "Shahid (store manager)",
        "remarks": "One carton refused — damaged corner",
        "delivered": [{"line_id": str(dn_lines[0].id), "qty_delivered": str(dn_lines[0].qty_loaded - 1), "qty_returned": "1", "return_reason": "damaged", "return_condition": "damaged"}],
    }
    r = api.post(f"/api/v1/delivery-notes/{note.pk}/deliver", payload, format="json")
    assert r.status_code == 200, r.content
    assert r.data["status"] == "partially_delivered" and r.data["receiver_name"].startswith("Shahid")
    assert r.data["remarks"] == "One carton refused — damaged corner" and r.data["delivered_at"]
    steps = _steps(api, order.pk)
    assert steps["delivered"]["state"] == "done"
    assert steps["delivered"]["detail"].startswith("Partially delivered")


def test_workflow_reads_the_old_flow_too(api, order_with_stock):
    """Mark-delivered (no load form, no trip) still produces a sensible chain."""
    order, warehouse, variant = order_with_stock
    r = api.post(f"/api/v1/sales-orders/{order.pk}/mark-delivered", {"receiver_name": "Walk-in"}, format="json")
    assert r.status_code == 200, r.content
    steps = _steps(api, order.pk)
    assert steps["delivered"]["state"] == "done" and steps["invoice"]["state"] == "done"
    assert steps["load_form"]["state"] in ("skipped", "todo", "current")
