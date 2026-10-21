"""Cash vs credit on orders, the rep's sales summary, and a rep hearing only their own orders' turns."""
from decimal import Decimal

import pytest

from apps.core.tenancy import tenant_context

pytestmark = pytest.mark.django_db

GULBERG = (31.5204, 74.3487)


@pytest.fixture
def rep_app(api, tenant):
    from apps.accounts.models import TenantMembership, User
    from apps.accounts.services import RoleService, UserService
    from apps.partners.models import SupplierRep
    from rest_framework.test import APIClient

    from .conftest import seed_basic

    wh, supplier, variant, customer = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        rep = SupplierRep.objects.create(supplier=supplier, code="R-CC", name="Cash Rep")
        customer.gps_lat, customer.gps_lng = Decimal(str(GULBERG[0])), Decimal(str(GULBERG[1]))
        customer.save(update_fields=["gps_lat", "gps_lng"])
        customer.assigned_reps.add(rep)
        role = RoleService.create_role(tenant, "CashCredit", ["external.view_assigned_customers", "external.submit_order",
                                                              "external.view_own_orders", "external.view_prices"],
                                       role_type="external")
        UserService.create_tenant_user(tenant, "Cash Rep", email="cc@probe.pk", password="Abcd-1234", roles=[role], warehouses=[])
        user = User.objects.get(email="cc@probe.pk")
        User.objects.filter(pk=user.pk).update(must_change_password=False)
        rep.user, rep.role = user, role
        rep.save(update_fields=["user", "role"])
        TenantMembership.objects.filter(user=user, tenant=tenant).update(supplier_rep=rep)

    phone = APIClient()
    login = phone.post("/api/v1/auth/login", {"identifier": "cc@probe.pk", "password": "Abcd-1234"}, format="json")
    assert login.status_code == 200, login.content
    phone.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}", HTTP_X_TENANT_ID=str(tenant.id))
    return {"tenant": tenant, "rep": rep, "customer": customer, "variant": variant, "phone": phone}


def _post_order(rep_app, mode=None, qty=2):
    body = {"customer": {"id": str(rep_app["customer"].id)}, "gps": {"lat": GULBERG[0], "lng": GULBERG[1], "accuracy_m": 5},
            "lines": [{"variant_id": str(rep_app["variant"].id), "qty": qty}]}
    if mode is not None:
        body["payment_mode"] = mode
    return rep_app["phone"].post("/ext/v1/orders", body, format="json")


def test_payment_mode_defaults_from_customer_terms(rep_app, tenant):
    r = _post_order(rep_app)  # seed customer has credit_days 0 → cash
    assert r.status_code == 201, r.content
    assert r.data["payment_mode"] == "cash"
    with tenant_context(tenant, tenant.owner):
        rep_app["customer"].credit_days = 15
        rep_app["customer"].save(update_fields=["credit_days"])
    r2 = _post_order(rep_app)
    assert r2.data["payment_mode"] == "credit"


def test_payment_mode_chosen_by_rep_and_validated(rep_app):
    assert _post_order(rep_app, mode="credit").data["payment_mode"] == "credit"
    bad = _post_order(rep_app, mode="instalments")
    assert bad.status_code in (400, 422)
    rows = rep_app["phone"].get("/ext/v1/orders").data["results"]
    assert all("payment_mode" in row for row in rows)


def test_sales_summary_splits_cash_and_credit(rep_app):
    assert _post_order(rep_app, mode="cash", qty=2).status_code == 201
    assert _post_order(rep_app, mode="credit", qty=3).status_code == 201
    s = rep_app["phone"].get("/ext/v1/me/sales-summary").data
    assert s["orders"] == 2
    assert Decimal(s["cash_total"]) == Decimal("300")   # 2 × 150
    assert Decimal(s["credit_total"]) == Decimal("450")  # 3 × 150
    assert Decimal(s["total"]) == Decimal("750")
    assert Decimal(s["cancelled_total"]) == 0


def test_rep_notified_of_own_order_turns_with_reasons(rep_app, tenant, api):
    from apps.core.models import Notification
    from apps.sales.services import OrderService

    r = _post_order(rep_app)
    with tenant_context(tenant, tenant.owner):
        from apps.sales.models import SalesOrder

        order = SalesOrder.objects.get(id=r.data["id"])
        user = rep_app["rep"].user
        types = lambda: set(Notification.objects.filter(user=user, data__order_id=str(order.id)).values_list("type", flat=True))
        assert "order.received" in types()
        OrderService._set(order, "under_review", "under_review")
        assert "order.under_review" in types()
        OrderService._set(order, "on_hold_credit", "on_hold_credit", {"reason": "limit"})
        held = Notification.objects.filter(user=user, type="order.on_hold").first()
        assert held is not None and "credit" in held.title
        OrderService._set(order, "confirmed", "confirmed")
        OrderService.cancel(order, "customer changed mind")
        assert "order.cancelled" in types()


def test_trip_reaches_rep_only_with_their_own_stop(rep_app, tenant):
    from apps.core.events import emit
    from apps.core.models import Notification
    from apps.delivery.models import DeliveryTrip
    from apps.partners.models import SupplierRep

    with tenant_context(tenant, tenant.owner):
        from apps.inventory.models import Warehouse

        trip = DeliveryTrip.objects.create(number="T-CC-1", date="2026-10-22", warehouse=Warehouse.objects.first(),
                                           rep=rep_app["rep"])
        trip.stops_count = 0
        emit("delivery.assigned", trip=trip)  # no stop of theirs on it
        assert not Notification.objects.filter(user=rep_app["rep"].user, data__trip_id=str(trip.id)).exists()
