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


def test_documents_inherit_the_order_number(api, order_with_stock):
    """A whole chain shares the order's counter: SO-0000N → PL-0000N, DN-0000N, INV-0000N."""
    from apps.core.numbering import numeric_core

    order, _wh, _variant = order_with_stock
    core = numeric_core(order.number)

    pl = api.post(f"/api/v1/sales-orders/{order.pk}/load-form", {}, format="json").data
    assert numeric_core(pl["number"]) == core, (pl["number"], order.number)

    issued = api.post(f"/api/v1/pick-lists/{pl['id']}/issue",
                      {"issues": [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in pl["lines"]]}, format="json")
    assert issued.status_code == 200, issued.content
    assert all(numeric_core(n["number"]) == core for n in issued.data["delivery_note_numbers"]), issued.data["delivery_note_numbers"]

    inv = api.post(f"/api/v1/sales-orders/{order.pk}/invoice", {}, format="json")
    assert inv.status_code == 201, inv.content
    assert numeric_core(inv.data[0]["number"]) == core, inv.data[0]["number"]


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

    # 2 · load form: SAVED for review (owner, 27 Oct 2026), then sent to the warehouse
    r = api.post(f"/api/v1/sales-orders/{order.pk}/load-form", {}, format="json")
    assert r.status_code == 201, r.content
    pl = r.data
    assert pl["status"] == "open" and pl["order_numbers"] == [order.number] and not pl["sent_at"]
    assert api.post(f"/api/v1/sales-orders/{order.pk}/load-form", {}, format="json").status_code == 422  # only one open form
    assert _steps(api, order.pk)["load_form"]["state"] == "current"
    sent = api.post(f"/api/v1/pick-lists/{pl['id']}/send", {}, format="json")
    assert sent.status_code == 200 and sent.data["status"] == "sent" and sent.data["sent_at"]

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

    # 4 · invoice for what was issued: saved as a draft, reviewed, then posted
    r = api.post(f"/api/v1/sales-orders/{order.pk}/invoice", {"post": False}, format="json")
    assert r.status_code == 201, r.content
    invoice = r.data[0]
    assert Decimal(invoice["total"]) > 0 and invoice["status"] == "draft"
    assert _steps(api, order.pk)["invoice"]["state"] == "current"
    assert api.post(f"/api/v1/invoices/{invoice['id']}/post", {}, format="json").status_code == 200
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
    # delivered with no invoice: the invoice step waits on its draft until someone posts it
    assert steps["delivered"]["state"] == "done" and steps["invoice"]["state"] == "current"
    assert steps["invoice"]["action"]["label"] == "Review & post"
    assert steps["load_form"]["state"] in ("skipped", "todo", "current")
    draft = steps["invoice"]["action"]["to"].rsplit("/", 1)[-1]
    assert api.post(f"/api/v1/invoices/{draft}/post", {}, format="json").status_code == 200
    assert _steps(api, order.pk)["invoice"]["state"] == "done"


def test_an_invoiced_order_can_still_be_dispatched(api, tenant, order_with_stock):
    """
    The invoice-before-dispatch flow: issue → invoice → dispatch. Once an order is fully invoiced its status is
    `invoiced`, and that status was missing from the dispatch eligibility check — so the goods were issued and
    invoiced but could not be put on a vehicle (SO-00004 in the field). Dispatch must still open.
    """
    from decimal import Decimal

    from apps.core.tenancy import tenant_context

    order, warehouse, variant = order_with_stock
    form = api.post(f"/api/v1/sales-orders/{order.pk}/load-form", {}, format="json").data
    form = form if isinstance(form, dict) else form[0]
    issues = [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in form["lines"]]
    assert api.post(f"/api/v1/pick-lists/{form['id']}/issue", {"issues": issues}, format="json").status_code == 200
    assert api.post(f"/api/v1/sales-orders/{order.pk}/invoice", {}, format="json").status_code == 201

    # the real SO-00004 reached `invoiced` (deliver-then-invoice on part of it), with the loaded note still
    # waiting for a vehicle — reproduce that state directly, which is what the fix is about
    with tenant_context(tenant, tenant.owner):
        order.refresh_from_db()
        order.status = "invoiced"
        order.save(update_fields=["status"])

    r = api.post(f"/api/v1/pick-lists/{form['id']}/create-dispatch", {}, format="json")
    assert r.status_code in (200, 201), r.content
    assert r.data.get("number"), "a dispatch (trip) should have been created"
    order.refresh_from_db()
    assert order.trip_id, "the invoiced order is now on a trip"
