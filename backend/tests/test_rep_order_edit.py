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


def test_a_rep_cannot_edit_an_office_order(rep_app, api, tenant):
    """
    A staff order for the rep's customer carries them as the sale person (auto-assigned from the customer), so
    it shows in their app — but it is the office's document: the write must be refused even though it is visible.
    """
    from apps.sales.services import OrderService

    with tenant_context(tenant, tenant.owner):
        other = OrderService.create(customer=rep_app["customer"], lines=[{"variant": rep_app["variant"], "qty": Decimal("3"), "unit_price": None}], source="staff")

    r = rep_app["phone"].patch(f"/ext/v1/orders/{other.pk}", {
        "lines": [{"variant_id": str(rep_app["variant"].id), "qty": 99}],
    }, format="json")
    assert r.status_code in (403, 404), r.content
    if r.status_code == 403:
        assert r.data.get("type") == "order_not_editable", r.data


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


def test_a_rep_allowed_to_order_from_anywhere_is_not_fenced(rep_app, tenant):
    """
    The owner's per-rep rule: some reps may take an order from any location, others only inside their areas.
    Setting `geofence_policy` on the rep overrides the company rule for them, and the app reads the effective
    rule from /ext/v1/my-areas, so an "anywhere" rep gets Submit everywhere with no app change.
    """
    # under the company's block rule, an order from Karachi is refused (the shop sits in Gulberg)
    far = {"lat": KARACHI[0], "lng": KARACHI[1], "accuracy_m": 9.0}
    refused = rep_app["phone"].post("/ext/v1/orders", {
        "customer": {"id": str(rep_app["customer"].id)}, "gps": far,
        "lines": [{"variant_id": str(rep_app["variant"].id), "qty": 1}],
    }, format="json")
    assert refused.status_code == 403 and refused.data["type"] == "outside_area"
    assert rep_app["phone"].get("/ext/v1/my-areas").data["policy"] == "block"

    with tenant_context(tenant, tenant.owner):
        rep_app["rep"].geofence_policy = "off"
        rep_app["rep"].save(update_fields=["geofence_policy"])

    assert rep_app["phone"].get("/ext/v1/my-areas").data["policy"] == "off", "the app reads the rep's own rule"
    allowed = rep_app["phone"].post("/ext/v1/orders", {
        "customer": {"id": str(rep_app["customer"].id)}, "gps": far,
        "lines": [{"variant_id": str(rep_app["variant"].id), "qty": 1}],
    }, format="json")
    assert allowed.status_code == 201, allowed.content


def test_the_order_detail_echoes_its_photo_labels(rep_app):
    """The app has no other way to check its own uploads: labels only, never the image data."""
    photo = "data:image/jpeg;base64,c2hvcC1ieXRlcw=="
    made = rep_app["phone"].post("/ext/v1/orders", {
        "customer": {"id": str(rep_app["customer"].id)},
        "gps": {"lat": GULBERG[0], "lng": GULBERG[1], "accuracy_m": 9.0},
        "lines": [{"variant_id": str(rep_app["variant"].id), "qty": 1}],
        "attachments": [{"data": photo, "kind": "shop"}, {"data": photo, "kind": "shelf"}],
    }, format="json")
    assert made.status_code == 201, made.content
    row = rep_app["phone"].get(f"/ext/v1/orders/{made.data['id']}").data
    assert [a["kind"] for a in row["attachments"]] == ["shop", "shelf"]
    assert row["attachments"][0]["name"].startswith("Shop front — ")
    assert all("data" not in a for a in row["attachments"])


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


def test_the_list_rows_say_whether_each_order_can_still_be_edited(rep_app):
    """The app draws its order list from the list endpoint, so the edit button needs the answer there too."""
    made = _order(rep_app)
    assert made.status_code == 201, made.content
    rows = rep_app["phone"].get("/ext/v1/orders").data["results"]
    row = next(r for r in rows if r["id"] == made.data["id"])
    assert row["can_edit"] is True
    assert row["editable_until"], "and when the window closes"


def test_the_date_filters_actually_filter(rep_app, tenant):
    """`until` and `date_from`/`date_to` used to be read by the app and silently ignored by the server —
    a filter that does nothing produces confidently wrong daily totals."""
    made = _order(rep_app)
    assert made.status_code == 201, made.content
    with tenant_context(tenant, tenant.owner):
        from apps.sales.models import SalesOrder

        old = timezone.localdate() - timedelta(days=10)
        SalesOrder.objects.filter(pk=made.data["id"]).update(order_date=old, updated_at=timezone.now() - timedelta(days=10))
    fresh = _order(rep_app, qty=7)
    assert fresh.status_code == 201, fresh.content

    phone = rep_app["phone"]
    today = timezone.localdate()
    only_today = phone.get(f"/ext/v1/orders?date_from={today}").data["results"]
    assert [r["id"] for r in only_today] == [fresh.data["id"]]
    only_old = phone.get(f"/ext/v1/orders?date_to={today - timedelta(days=5)}").data["results"]
    assert [r["id"] for r in only_old] == [made.data["id"]]
    before_now = phone.get("/ext/v1/orders", {"until": (timezone.now() - timedelta(days=5)).isoformat()}).data["results"]
    assert [r["id"] for r in before_now] == [made.data["id"]]


def test_order_photos_carry_what_they_show(rep_app, tenant):
    """Two photos are required at the counter — shop front and shelf stock — and the office must be able to
    tell them apart. A labelled entry is {data, kind}; a bare string with attachment_kinds works the same."""
    import base64 as b64mod

    from apps.core.models import Attachment

    data = "data:image/jpeg;base64," + b64mod.b64encode(b"shop-bytes").decode()
    made = rep_app["phone"].post("/ext/v1/orders", {
        "customer": {"id": str(rep_app["customer"].id)},
        "gps": {"lat": GULBERG[0], "lng": GULBERG[1], "accuracy_m": 9.0},
        "lines": [{"variant_id": str(rep_app["variant"].id), "qty": 1}],
        "attachments": [{"data": data, "kind": "shop"}, data],
        "attachment_kinds": [None, "shelf"],
    }, format="json")
    assert made.status_code == 201, made.content

    with tenant_context(tenant, tenant.owner):
        atts = {a.kind: a.name for a in Attachment.objects.filter(model="SalesOrder", object_id=made.data["id"])}
    assert set(atts) == {"visit_photo:shop", "visit_photo:shelf"}, atts
    assert atts["visit_photo:shop"].startswith("Shop front — ")
    assert atts["visit_photo:shelf"].startswith("Shelf stock — ")


def test_asking_for_a_pdf_with_the_pdf_accept_header_is_not_refused(rep_app):
    """`Accept: application/pdf` is the honest header for a PDF endpoint; it used to meet a 406 before the
    handler ran, because content negotiation only knew JSON."""
    made = _order(rep_app)
    assert made.status_code == 201, made.content
    r = rep_app["phone"].get(f"/ext/v1/orders/{made.data['id']}/pdf", HTTP_ACCEPT="application/pdf")
    assert r.status_code == 200 and r["Content-Type"] == "application/pdf", (r.status_code, r.headers)
    # and an error under that same header is still a JSON body, not a 500
    miss = rep_app["phone"].get(f"/ext/v1/orders/{made.data['id']}/invoice-pdf", HTTP_ACCEPT="application/pdf")
    assert miss.status_code == 404 and miss.data["type"] == "no_invoice"


def test_the_rep_can_count_where_their_goods_are_without_parsing_prose(rep_app, api, tenant):
    """
    The phone's dashboard needs numbers: what waits on the office, and what is on a vehicle right now.
    `delivery_state` is the dispatch stage as a code (the prose `delivery_status` derives from it), and the
    dashboard's rep block counts the same rule server-side, so the card and the list cannot disagree.
    """
    from apps.delivery.models import Driver, Vehicle
    from apps.sales.services import OrderService

    waiting = _order(rep_app)
    moving = _order(rep_app, qty=3)
    assert waiting.status_code == 201 and moving.status_code == 201

    with tenant_context(tenant, tenant.owner):
        from apps.sales.models import SalesOrder

        OrderService.confirm(SalesOrder.objects.get(pk=moving.data["id"]))
        vehicle = Vehicle.objects.create(reg_no="LEB-7777", type="van")
        driver = Driver.objects.create(name="Basit", phone="03009998877")
        wh_id = SalesOrder.objects.get(pk=moving.data["id"]).warehouse_id

    pl = api.post(f"/api/v1/sales-orders/{moving.data['id']}/load-form", {}, format="json")
    assert pl.status_code == 201, pl.content
    issues = [{"line_id": line["id"], "qty_issued": line["qty_to_pick"]} for line in pl.data["lines"]]
    assert api.post(f"/api/v1/pick-lists/{pl.data['id']}/issue", {"issues": issues}, format="json").status_code == 200
    trip = api.post("/api/v1/trips", {"warehouse": str(wh_id), "driver": str(driver.id), "vehicle": str(vehicle.id), "order_ids": [moving.data["id"]]}, format="json")
    assert trip.status_code == 201, trip.content

    phone = rep_app["phone"]
    rows = {r["id"]: r for r in phone.get("/ext/v1/orders").data["results"]}
    assert rows[moving.data["id"]]["delivery_state"] == "on_trip"
    assert rows[moving.data["id"]]["delivery_status"].startswith("On trip ")
    assert rows[waiting.data["id"]]["delivery_state"] == ""
    rep = phone.get("/api/v1/dashboard").data["rep"]
    assert rep["awaiting_office"] == 1, "only the untouched order still waits on the office"
    assert (rep["on_trip"], rep["out_for_delivery"]) == (1, 0)
    assert rep["pending"] == 2, "pending stays the wide OPEN set — confirmed and dispatched included"

    assert api.post(f"/api/v1/trips/{trip.data['id']}/start", {"odometer_start": 500}, format="json").status_code == 200
    rows = {r["id"]: r for r in phone.get("/ext/v1/orders").data["results"]}
    assert rows[moving.data["id"]]["delivery_state"] == "out_for_delivery"
    rep = phone.get("/api/v1/dashboard").data["rep"]
    assert (rep["on_trip"], rep["out_for_delivery"]) == (0, 1)


def test_a_rep_can_fetch_their_own_order_pdf_but_not_an_office_one(rep_app, tenant):
    """The app's print/share sheet needs the document: own order -> PDF; an office order -> 404, same as detail."""
    from apps.sales.services import OrderService

    made = _order(rep_app)
    assert made.status_code == 201, made.content
    own_id, own_number = made.data["id"], made.data["number"]
    r = rep_app["phone"].get(f"/ext/v1/orders/{own_id}/pdf")
    assert r.status_code == 200 and r["Content-Type"] == "application/pdf", r.status_code
    assert own_number in r["Content-Disposition"]

    r2 = rep_app["phone"].get(f"/ext/v1/orders/{own_id}/invoice-pdf")
    assert r2.status_code == 404 and r2.data["type"] == "no_invoice"

    with tenant_context(tenant, tenant.owner):
        other = OrderService.create(customer=rep_app["customer"], lines=[{"variant": rep_app["variant"], "qty": Decimal("1"), "unit_price": None}], source="staff")
        other.rep = None
        other.save(update_fields=["rep"])
    assert rep_app["phone"].get(f"/ext/v1/orders/{other.pk}/pdf").status_code == 404
