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

    # since 21 Oct 2026 an edit past the load form does not refuse — it pulls the paperwork back and the
    # office re-issues from the edited lines (the full flow is proven in test_stock_conservation)
    assert api.post(f"/api/v1/sales-orders/{r.data['id']}/load-form", {}, format="json").status_code == 201
    late = api.patch(f"/api/v1/sales-orders/{r.data['id']}", {"lines": [{"variant": str(v.id), "qty": "1"}]}, format="json")
    assert late.status_code == 200, late.content
    assert str(late.data["lines"][0]["qty_ordered"]).startswith("1")


def test_the_customers_credit_days_are_the_invoice_due_date(api, tenant):
    """The owner's rule (24 Oct 2026): due date = invoice date + the customer's credit days; zero days is a
    cash sale and the documents show no due date at all."""
    from datetime import timedelta

    from django.utils import timezone

    from .conftest import seed_basic

    wh, _sup, v, cust = seed_basic(tenant)
    assert not cust.credit_days, "the fixture customer is a cash customer"

    def invoice_for(order_id):
        pl = api.post(f"/api/v1/sales-orders/{order_id}/load-form", {}, format="json")
        issues = [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in pl.data["lines"]]
        api.post(f"/api/v1/pick-lists/{pl.data['id']}/issue", {"issues": issues}, format="json")
        made = api.post(f"/api/v1/sales-orders/{order_id}/invoice", {}, format="json")
        return made.data[0] if isinstance(made.data, list) else made.data

    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True,
                                          "lines": [{"variant": str(v.id), "qty": "2"}]}, format="json")
    cash = invoice_for(r.data["id"])
    assert cash["due_date"] == cash["date"], (cash["due_date"], cash["date"])

    api.patch(f"/api/v1/customers/{cust.id}", {"credit_days": 15}, format="json")
    r2 = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True,
                                           "lines": [{"variant": str(v.id), "qty": "2"}]}, format="json")
    credit = invoice_for(r2.data["id"])
    assert credit["due_date"] == str(timezone.localdate() + timedelta(days=15)), credit["due_date"]


def test_a_product_with_nothing_available_cannot_be_put_on_an_order(api, tenant):
    """The owner's rule: zero stock = not addable at all; a partial shortage still holds at confirmation."""
    from apps.catalog.models import Product, ProductVariant

    from apps.core.tenancy import tenant_context

    wh, sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        bare = Product.objects.create(name="Ghost Shampoo", sku="GHOST-1", base_uom=v.product.base_uom, tax_class=v.product.tax_class)
        ghost = ProductVariant.objects.create(product=bare, name="Ghost Shampoo", sku="GHOST-1-V")

    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id),
                                          "lines": [{"variant": str(ghost.id), "qty": "1"}]}, format="json")
    assert r.status_code in (400, 422), r.content
    assert "out of stock" in r.data["detail"], r.data


def test_only_the_owner_changes_a_confirmed_order(api, tenant):
    """The owner's rule (21 Oct 2026): a confirmed order is theirs to change, nobody else's."""
    from rest_framework.test import APIClient

    from apps.accounts.models import User
    from apps.accounts.services import RoleService, UserService

    wh, _sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        role = RoleService.create_role(tenant, "Order Clerk", ["sales.view_sales_order", "sales.add_sales_order", "sales.change_sales_order"])
        UserService.create_tenant_user(tenant, "Clerk", email="clerk@probe.pk", password="Abcd-1234", roles=[role], warehouses=[])
        User.objects.filter(email="clerk@probe.pk").update(must_change_password=False)

    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True,
                                          "lines": [{"variant": str(v.id), "qty": "2"}]}, format="json")
    assert r.status_code == 201 and r.data["status"] == "confirmed"

    clerk = APIClient()
    login = clerk.post("/api/v1/auth/login", {"identifier": "clerk@probe.pk", "password": "Abcd-1234"}, format="json")
    clerk.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}", HTTP_X_TENANT_ID=str(tenant.id))
    blocked = clerk.patch(f"/api/v1/sales-orders/{r.data['id']}", {"lines": [{"variant": str(v.id), "qty": "9"}]}, format="json")
    assert blocked.status_code == 403 and "owner" in blocked.data["detail"], blocked.content

    fixed = api.patch(f"/api/v1/sales-orders/{r.data['id']}", {"lines": [{"variant": str(v.id), "qty": "9"}]}, format="json")
    assert fixed.status_code == 200, fixed.content


def test_the_office_hears_a_delivery(tenant):
    """'in notification show new order and delivered notification' — the delivered half, to the office."""
    from apps.core.models import Notification
    from apps.sales.handlers import _order_delivered
    from apps.sales.services import OrderService
    from decimal import Decimal

    wh, _sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        order = OrderService.create(customer=cust, lines=[{"variant": v, "qty": Decimal("2"), "unit_price": None}], source="staff", warehouse=wh)
        _order_delivered(order)
        n = Notification.objects.filter(type="delivery.completed", user=tenant.owner).order_by("-created_at").first()
        assert n is not None and order.number in n.title


def test_cancel_after_confirmation_is_the_owners_and_the_stock_comes_back(api, tenant):
    """
    The owner's rule (21 Oct 2026): once confirmed, cancelling is the owner's call — delegable only through
    the "Cancel after confirmation" switch. And a cancelled order never leaves stock written out: goods the
    warehouse already issued return as their own movement, the delivery note and load form die with it.
    """
    from decimal import Decimal

    from rest_framework.test import APIClient

    from apps.accounts.models import User
    from apps.accounts.services import RoleService, UserService
    from apps.inventory.models import StockMovement
    from apps.inventory.services import StockService

    wh, _sup, v, cust = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        role = RoleService.create_role(tenant, "Cancel Clerk", ["sales.view_sales_order", "sales.add_sales_order", "sales.delete_sales_order", "sales.cancel_sales_order"])
        UserService.create_tenant_user(tenant, "Clerk2", email="clerk2@probe.pk", password="Abcd-1234", roles=[role], warehouses=[])
        User.objects.filter(email="clerk2@probe.pk").update(must_change_password=False)
        before = StockService.available(v, wh)

    r = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id), "confirm": True,
                                          "lines": [{"variant": str(v.id), "qty": "4"}]}, format="json")
    assert r.status_code == 201 and r.data["status"] == "confirmed"
    pl = api.post(f"/api/v1/sales-orders/{r.data['id']}/load-form", {}, format="json")
    issues = [{"line_id": l["id"], "qty_issued": l["qty_to_pick"]} for l in pl.data["lines"]]
    assert api.post(f"/api/v1/pick-lists/{pl.data['id']}/issue", {"issues": issues}, format="json").status_code == 200

    clerk = APIClient()
    login = clerk.post("/api/v1/auth/login", {"identifier": "clerk2@probe.pk", "password": "Abcd-1234"}, format="json")
    clerk.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}", HTTP_X_TENANT_ID=str(tenant.id))
    blocked = clerk.post(f"/api/v1/sales-orders/{r.data['id']}/cancel", {"reason": "dup"}, format="json")
    assert blocked.status_code == 403 and "owner" in blocked.data["detail"], blocked.content

    done = api.post(f"/api/v1/sales-orders/{r.data['id']}/cancel", {"reason": "customer backed out"}, format="json")
    assert done.status_code == 200 and done.data["status"] == "cancelled", done.content

    with tenant_context(tenant, tenant.owner):
        assert StockService.available(v, wh) == before, "every unit the warehouse issued is back"
        mv = StockMovement.objects.filter(movement_type="issue_cancelled", variant=v).first()
        assert mv is not None and mv.qty == Decimal("4"), "the reversal says what it is"
        from apps.delivery.models import DeliveryNote, PickList

        assert not DeliveryNote.objects.filter(order_id=r.data["id"]).exclude(status="cancelled").exists()
        assert PickList.objects.get(pk=pl.data["id"]).status == "cancelled"

    # pre-confirmation, the ordinary Delete switch still cancels
    r2 = api.post("/api/v1/sales-orders", {"customer": str(cust.id), "warehouse": str(wh.id),
                                           "lines": [{"variant": str(v.id), "qty": "1"}]}, format="json")
    ok = clerk.post(f"/api/v1/sales-orders/{r2.data['id']}/cancel", {"reason": "typo"}, format="json")
    assert ok.status_code == 200, ok.content
