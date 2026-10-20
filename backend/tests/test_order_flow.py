"""Order → confirm → trip → dispatch (stock out) → deliver → invoice → receipt; rep collection & settlement."""
import pytest
from decimal import Decimal

from apps.core.tenancy import tenant_context

from .conftest import seed_basic

pytestmark = pytest.mark.django_db


def test_order_to_invoice_via_trip(tenant):
    from apps.accounting.services import PaymentService, ReportQueries, account_for
    from apps.delivery.models import Driver, Vehicle
    from apps.delivery.services import DeliveryService, TripService
    from apps.inventory.services import StockService
    from apps.sales.models import SalesInvoice
    from apps.sales.services import OrderService

    wh, sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        order = OrderService.create(customer=cust, lines=[{"variant": v, "qty": Decimal("10"), "unit_price": None}], source="staff", warehouse=wh)
        assert order.status == "submitted" and order.total == Decimal("1500")
        OrderService.confirm(order)
        assert order.status == "confirmed"
        on_hand, reserved = StockService.on_hand(v, wh)
        assert (on_hand, reserved) == (Decimal("100"), Decimal("10"))
        driver = Driver.objects.create(name="D", phone="1")
        vehicle = Vehicle.objects.create(reg_no="V-1")
        trip = TripService.create(warehouse=wh, driver=driver, vehicle=vehicle, orders=[order])
        TripService.start(trip)
        order.refresh_from_db()
        assert order.status == "dispatched" and trip.status == "out_for_delivery"
        on_hand, reserved = StockService.on_hand(v, wh)
        assert (on_hand, reserved) == (Decimal("90"), Decimal("0"))
        dn = trip.delivery_notes.first()
        DeliveryService.deliver(dn, receiver_name="R", cash_collected=Decimal("500"))
        order.refresh_from_db()
        inv = SalesInvoice.objects.get(order=order)
        assert order.status == "invoiced" and inv.total == Decimal("1500") and inv.paid_amount == Decimal("500")
        assert inv.journal is not None and inv.journal.status == "posted"
        # AR = 1000 outstanding, COGS = 10 × 100
        assert ReportQueries.account_balance(account_for("ar")) == Decimal("1000")
        assert ReportQueries.account_balance(account_for("cogs")) == Decimal("1000")
        TripService.close(trip)
        assert trip.status == "closed" and trip.reconciliation["ok"] is True
        pay = PaymentService.record(direction="in", partner_type="customer", partner_id=str(cust.id), amount=Decimal("1000"), method="cash")
        inv.refresh_from_db()
        assert inv.status == "paid" and pay.unallocated == 0
        assert ReportQueries.account_balance(account_for("ar")) == Decimal("0")


def test_mark_delivered_without_trip_and_rep_collection(tenant):
    from apps.accounting.services import ReportQueries, account_for
    from apps.partners.models import SupplierRep
    from apps.sales.models import SalesInvoice
    from apps.sales.services import OrderService, RepCollectionService

    wh, sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        rep = SupplierRep.objects.create(supplier=sup, code="R1", name="Rep")
        cust.assigned_reps.add(rep)
        order = OrderService.create(customer=cust, lines=[{"variant": v, "qty": Decimal("4"), "unit_price": None}], source="rep_app", rep=rep, supplier=sup, warehouse=wh)
        OrderService.confirm(order)
        OrderService.mark_delivered(order, receiver_name="Owner")
        order.refresh_from_db()
        inv = SalesInvoice.objects.get(order=order)
        assert order.status == "invoiced" and inv.total == Decimal("600")
        col = RepCollectionService.record(rep=rep, customer=cust, amount=Decimal("600"))
        inv.refresh_from_db()
        assert inv.status == "paid" and col.status == "held_by_rep"
        assert ReportQueries.account_balance(account_for("rep_clearing")) == Decimal("600")
        st = RepCollectionService.settle(rep=rep, collections=[col])
        col.refresh_from_db()
        assert col.status == "settled" and st.amount == Decimal("600")
        assert ReportQueries.account_balance(account_for("rep_clearing")) == Decimal("0")
        assert ReportQueries.account_balance(account_for("cash")) == Decimal("600")


def test_insufficient_stock_puts_order_on_hold(tenant):
    from apps.sales.services import OrderService

    wh, sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        order = OrderService.create(customer=cust, lines=[{"variant": v, "qty": Decimal("500"), "unit_price": None}], source="staff", warehouse=wh)
        OrderService.confirm(order)
        assert order.status == "on_hold_stock"


def test_external_order_api_by_barcode(api, tenant):
    wh, sup, v, cust = seed_basic(tenant)
    r = api.post("/ext/v1/orders", {"external_ref": "X-1", "customer": {"code": "C-1"}, "lines": [{"barcode": "1234567890123", "qty": 3}]}, format="json", HTTP_IDEMPOTENCY_KEY="k1")
    assert r.status_code == 201, r.content
    assert r.data["status"] == "submitted" and r.data["lines"][0]["qty_ordered"] == "3.000"
    r2 = api.post("/ext/v1/orders", {"external_ref": "X-1", "customer": {"code": "C-1"}, "lines": [{"barcode": "1234567890123", "qty": 3}]}, format="json", HTTP_IDEMPOTENCY_KEY="k1")
    assert r2["Idempotent-Replayed"] == "true" and r2.data["number"] == r.data["number"]


def test_mark_delivered_via_api_moves_order_to_invoiced(api, tenant):
    """Regression: the view prefetches lines; the service must still see fresh quantities."""
    wh, sup, v, cust = seed_basic(tenant)
    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True, "lines": [{"variant": str(v.id), "qty": "5"}]}, format="json")
    assert r.status_code == 201 and r.data["status"] == "confirmed", r.content
    r2 = api.post(f"/api/v1/sales-orders/{r.data['id']}/mark-delivered", {"receiver_name": "Owner", "cash_collected": "0"}, format="json")
    assert r2.status_code == 200, r2.content
    assert r2.data["status"] == "invoiced" and r2.data["invoices"][0]["total"] == "750.0000"


def test_a_draft_order_can_be_deleted_but_a_processed_one_cannot(api, tenant):
    """Delete is for an order that never went anywhere; a confirmed/invoiced order is cancelled, not deleted."""
    from .conftest import seed_basic

    wh, _sup, variant, customer = seed_basic(tenant)

    draft = api.post("/api/v1/sales-orders", {"customer": str(customer.id), "warehouse": str(wh.id),
                                              "lines": [{"variant": str(variant.id), "qty": "2"}]}, format="json")
    assert draft.status_code == 201 and draft.data["status"] in ("draft", "submitted"), draft.content
    gone = api.delete(f"/api/v1/sales-orders/{draft.data['id']}")
    assert gone.status_code == 204, gone.content
    assert api.get(f"/api/v1/sales-orders/{draft.data['id']}").status_code == 404

    confirmed = api.post("/api/v1/sales-orders", {"customer": str(customer.id), "warehouse": str(wh.id), "confirm": True,
                                                  "lines": [{"variant": str(variant.id), "qty": "2"}]}, format="json")
    assert confirmed.status_code == 201 and confirmed.data["status"] == "confirmed"
    refused = api.delete(f"/api/v1/sales-orders/{confirmed.data['id']}")
    assert refused.status_code == 400 and "cancel it instead" in str(refused.data)


def test_deleting_an_order_needs_the_delete_right(tenant):
    """The Delete button is gated on the sales-orders delete permission, on the server too."""
    from apps.core.tenancy import tenant_context

    from .test_permission_matrix import _client

    with tenant_context(tenant, tenant.owner):
        from .conftest import seed_basic

        wh, _sup, variant, customer = seed_basic(tenant)
        # `_client` re-points one shared probe role, so grant the rights in sequence on the same client
        client = _client(tenant, ["sales.view_sales_order", "sales.add_sales_order"])

    made = client.post("/api/v1/sales-orders", {"customer": str(customer.id), "warehouse": str(wh.id),
                                                "lines": [{"variant": str(variant.id), "qty": "1"}]}, format="json")
    assert made.status_code == 201, made.content
    # without the delete right the button is hidden and the API refuses
    assert client.delete(f"/api/v1/sales-orders/{made.data['id']}").status_code == 403
    # grant it (re-points the same role) and it goes through
    with tenant_context(tenant, tenant.owner):
        client = _client(tenant, ["sales.view_sales_order", "sales.add_sales_order", "sales.delete_sales_order"])
    assert client.delete(f"/api/v1/sales-orders/{made.data['id']}").status_code == 204


def test_a_confirmed_order_is_still_editable_until_the_warehouse_starts(api, tenant):
    """Owner's rule (19 Oct 2026): a mistake in a confirmed order gets fixed, not cancelled — but once a
    load form exists the warehouse is working from it, and the edit is refused."""
    from .conftest import seed_basic

    wh, _sup, v, cust = seed_basic(tenant)
    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True,
                                          "lines": [{"variant": str(v.id), "qty": "2"}]}, format="json")
    assert r.status_code == 201 and r.data["status"] == "confirmed", r.content

    fixed = api.patch(f"/api/v1/sales-orders/{r.data['id']}", {"lines": [{"variant": str(v.id), "qty": "7"}]}, format="json")
    assert fixed.status_code == 200, fixed.content
    assert fixed.data["status"] == "confirmed", "an edit corrects the lines, it does not reopen the order"
    assert str(fixed.data["lines"][0]["qty_ordered"]).startswith("7")
    assert str(fixed.data["lines"][0]["qty_allocated"]).startswith("7"), "the new quantity holds the stock"

    assert api.post(f"/api/v1/sales-orders/{r.data['id']}/load-form", {}, format="json").status_code == 201
    late = api.patch(f"/api/v1/sales-orders/{r.data['id']}", {"lines": [{"variant": str(v.id), "qty": "1"}]}, format="json")
    assert late.status_code in (400, 422) and "load form" in late.data["detail"], late.content


def test_an_invoice_falls_due_in_45_days_unless_the_customer_has_terms(api, tenant):
    """The owner's figure: no credit days on the customer means 45 days, not due-on-issue."""
    from datetime import timedelta

    from django.utils import timezone

    from .conftest import seed_basic

    wh, _sup, v, cust = seed_basic(tenant)
    assert not cust.credit_days, "the fixture customer carries no terms of its own"
    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True,
                                          "lines": [{"variant": str(v.id), "qty": "2"}]}, format="json")
    pl = api.post(f"/api/v1/sales-orders/{r.data['id']}/load-form", {}, format="json")
    issues = [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in pl.data["lines"]]
    api.post(f"/api/v1/pick-lists/{pl.data['id']}/issue", {"issues": issues}, format="json")
    made = api.post(f"/api/v1/sales-orders/{r.data['id']}/invoice", {}, format="json")
    inv = made.data[0] if isinstance(made.data, list) else made.data
    assert inv["due_date"] == str(timezone.localdate() + timedelta(days=45)), inv["due_date"]
