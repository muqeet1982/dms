"""
A rep correcting their own order, and a shop added outside the area they cover.

Both are the same principle, which this system keeps returning to: an **order** is a transaction and the
working-area rule guards it, while an **observation** — a visit, a new shop, a correction typed from the next
street — is evidence, and refusing to record it destroys the thing you wanted.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.core.tenancy import tenant_context

pytestmark = pytest.mark.django_db

GULBERG = (31.5180, 74.3480)
KARACHI = (24.8607, 67.0011)


@pytest.fixture
def rep_app(api, tenant):
    """A rep with a login, a shop inside their area, and an area to be outside of."""
    from apps.accounts.models import TenantMembership, User
    from apps.accounts.services import RoleService, UserService
    from apps.partners.models import GeoFence, GeoFenceRep, SupplierRep
    from rest_framework.test import APIClient

    from .conftest import seed_basic

    wh, supplier, variant, customer = seed_basic(tenant)
    with tenant_context(tenant, tenant.owner):
        rep = SupplierRep.objects.create(supplier=supplier, code="R-EDT", name="Editing Rep")
        customer.gps_lat, customer.gps_lng = Decimal(str(GULBERG[0])), Decimal(str(GULBERG[1]))
        customer.save(update_fields=["gps_lat", "gps_lng"])
        customer.assigned_reps.add(rep)
        fence = GeoFence.objects.create(name="Gulberg", type="circle", centre_lat=Decimal(str(GULBERG[0])),
                                        centre_lng=Decimal(str(GULBERG[1])), radius_m=3000)
        GeoFenceRep.objects.create(fence=fence, rep=rep)
        role = RoleService.create_role(tenant, "Editing", ["external.view_assigned_customers", "external.submit_order",
                                                           "external.view_own_orders", "external.add_customer_request"],
                                       role_type="external")
        UserService.create_tenant_user(tenant, "Editing Rep", email="edit@probe.pk", password="Abcd-1234", roles=[role], warehouses=[])
        user = User.objects.get(email="edit@probe.pk")
        User.objects.filter(pk=user.pk).update(must_change_password=False)
        rep.user, rep.role = user, role
        rep.save(update_fields=["user", "role"])
        TenantMembership.objects.filter(user=user, tenant=tenant).update(supplier_rep=rep)

    phone = APIClient()
    login = phone.post("/api/v1/auth/login", {"identifier": "edit@probe.pk", "password": "Abcd-1234"}, format="json")
    assert login.status_code == 200, login.content
    phone.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}", HTTP_X_TENANT_ID=str(tenant.id))
    return {"tenant": tenant, "rep": rep, "customer": customer, "variant": variant, "phone": phone}


def _order(rep_app, qty=2):
    return rep_app["phone"].post("/ext/v1/orders", {
        "customer": {"id": str(rep_app["customer"].id)},
        "gps": {"lat": GULBERG[0], "lng": GULBERG[1], "accuracy_m": 9.0},
        "lines": [{"variant_id": str(rep_app["variant"].id), "qty": qty}],
    }, format="json")


def test_a_rep_fixes_a_mistyped_quantity_instead_of_rebuilding_the_basket(rep_app):
    """Before this there was no edit at all: a rep who typed 2 for 20 had to cancel and start again."""
    made = _order(rep_app, qty=2)
    assert made.status_code == 201, made.content
    assert made.data["lines"][0]["qty_base"] == "2.000"

    fixed = rep_app["phone"].patch(f"/ext/v1/orders/{made.data['id']}", {
        "lines": [{"variant_id": str(rep_app["variant"].id), "qty": 20}],
        "notes": "typed 2, meant 20",
    }, format="json")
    assert fixed.status_code == 200, fixed.content
    assert fixed.data["lines"][0]["qty_base"] == "20.000"
    assert fixed.data["number"] == made.data["number"], "the same order, not a new one"
    assert fixed.data["notes"] == "typed 2, meant 20"


def test_the_order_says_whether_it_can_still_be_edited(rep_app):
    """The app shows the button off this, so a rep never rebuilds a basket to meet a refusal."""
    made = _order(rep_app)
    row = rep_app["phone"].get(f"/ext/v1/orders/{made.data['id']}")
    assert row.data["can_edit"] is True
    assert row.data["editable_until"], "and when it closes"


def test_a_correction_from_the_next_street_is_not_an_area_violation(rep_app):
    """The rep has left the shop by the time they notice — that is the ordinary case, not a suspicious one."""
    made = _order(rep_app)
    # the original order carries where it was taken; the correction carries no position at all
    far = rep_app["phone"].patch(f"/ext/v1/orders/{made.data['id']}", {
        "lines": [{"variant_id": str(rep_app["variant"].id), "qty": 5}],
    }, format="json")
    assert far.status_code == 200, far.content
    assert far.data["gps"] == {"lat": GULBERG[0], "lng": GULBERG[1]}, "where it was taken is untouched"


def test_after_midnight_the_day_is_closed(rep_app):
    made = _order(rep_app)
    with tenant_context(rep_app["tenant"], rep_app["tenant"].owner):
        from apps.sales.models import SalesOrder

        order = SalesOrder.objects.get(pk=made.data["id"])
        # the window runs from when it was taken, so that is what has to be yesterday
        SalesOrder.objects.filter(pk=order.pk).update(created_at=order.created_at - timedelta(days=2),
                                                      order_date=timezone.localdate() - timedelta(days=2))

    late = rep_app["phone"].patch(f"/ext/v1/orders/{made.data['id']}", {
        "lines": [{"variant_id": str(rep_app["variant"].id), "qty": 9}],
    }, format="json")
    assert late.status_code == 403 and late.data["type"] == "edit_window_closed", late.content
    assert "can no longer be changed" in late.data["detail"]

    row = rep_app["phone"].get(f"/ext/v1/orders/{made.data['id']}")
    assert row.data["can_edit"] is False


def test_once_the_office_confirms_it_the_rep_cannot_change_it(rep_app, api):
    """Confirming reserves stock and the warehouse may be picking — an edit behind that moves the goalposts."""
    made = _order(rep_app)
    with tenant_context(rep_app["tenant"], rep_app["tenant"].owner):
        from apps.sales.models import SalesOrder
        from apps.sales.services import OrderService

        OrderService.confirm(SalesOrder.objects.get(pk=made.data["id"]))

    blocked = rep_app["phone"].patch(f"/ext/v1/orders/{made.data['id']}", {
        "lines": [{"variant_id": str(rep_app["variant"].id), "qty": 1}],
    }, format="json")
    assert blocked.status_code == 403 and blocked.data["type"] == "order_not_editable", blocked.content
    assert "office" in blocked.data["detail"]


def test_a_rep_cannot_edit_somebody_elses_order(rep_app, api, tenant):
    """The rep's own orders are scoped by rep, and that scoping has to hold on the write too."""
    from apps.sales.services import OrderService

    with tenant_context(tenant, tenant.owner):
        other = OrderService.create(customer=rep_app["customer"], lines=[{"variant": rep_app["variant"], "qty": Decimal("3"), "unit_price": None}], source="staff")

    r = rep_app["phone"].patch(f"/ext/v1/orders/{other.pk}", {
        "lines": [{"variant_id": str(rep_app["variant"].id), "qty": 99}],
    }, format="json")
    assert r.status_code == 404, r.content


def test_a_shop_outside_the_area_is_kept_with_the_distance_on_it(rep_app):
    """
    A rep is told about a shop in the next market, or adds one from the office. Refusing the write destroys
    the record; the shop arrives `pending`, so the office decides — and can now see how far out it was.
    """
    r = rep_app["phone"].post("/ext/v1/customers", {
        "name": "Far Away Beauty", "type": "retailer", "phone": "03007654321",
        "ntn": "1234567-8", "cnic": "35202-1234567-1",
        "gps": {"lat": KARACHI[0], "lng": KARACHI[1], "accuracy_m": 12.0},
    }, format="json")
    assert r.status_code == 201, r.content
    assert r.data["status"] == "pending"
    assert r.data["inside_area"] is False
    assert r.data["distance_m"] > 1_000_000, r.data["distance_m"]

    # and an order from that same spot is still refused — the distinction is the point
    order = rep_app["phone"].post("/ext/v1/orders", {
        "customer": {"id": r.data["id"]},
        "gps": {"lat": KARACHI[0], "lng": KARACHI[1], "accuracy_m": 12.0},
        "lines": [{"variant_id": str(rep_app["variant"].id), "qty": 1}],
    }, format="json")
    assert order.status_code == 403 and order.data["type"] == "outside_area"


def test_the_shop_licence_numbers_are_stored_instead_of_silently_dropped(rep_app):
    """NTN and CNIC were accepted with a 201 and thrown away — the worst of the three possible behaviours."""
    r = rep_app["phone"].post("/ext/v1/customers", {
        "name": "Registered Salon", "type": "salon",
        "ntn": "1234567-8", "strn": "11-22-3344-555-66", "cnic": "35202-1234567-1",
        "gps": {"lat": GULBERG[0], "lng": GULBERG[1]},
    }, format="json")
    assert r.status_code == 201, r.content
    assert (r.data["ntn"], r.data["cnic"], r.data["strn"]) == ("1234567-8", "35202-1234567-1", "11-22-3344-555-66")

    listed = next(c for c in rep_app["phone"].get("/ext/v1/customers").data["results"] if c["id"] == r.data["id"])
    assert listed["ntn"] == "1234567-8" and listed["cnic"] == "35202-1234567-1"
